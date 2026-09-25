"""The Library: the account as Meta structures it, campaign > ad set > ad.

Every number is the ad's own 30-day rollup (ad_metric_30d), summed up the tree, so a campaign
reads exactly the sum of its ad sets and an ad set the sum of its ads. A file that runs in
three campaigns counts in each only for the ads it has there, never with its footage-wide
totals.

Ad sets and campaigns have no synced status of their own (adset_history is not synced), so a
node is live when any ad under it is: an ad's effective_status already folds in its ad set's
and campaign's switches (ADSET_PAUSED, CAMPAIGN_PAUSED).
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.repo.creatives import CLICK_FLOOR, IMPRESSION_FLOOR, _search_pattern

METRIC_COLS = (
    "spend",
    "impressions",
    "clicks",
    "link_clicks",
    "video_3s_views",
    "purchases",
    "conversions",
    "conversion_value",
)

# Which ads a view lists: those that delivered in the window (the default, and what every
# number on the page covers), those Meta is delivering now, or every ad.
DELIVERY = ("spent", "live", "all")

_SEEN = f"x.impressions >= {IMPRESSION_FLOOR}"
_CLICKED = f"({_SEEN} AND x.link_clicks >= {CLICK_FLOOR})"
# Same sorts and floors as the creatives grid (cs.core.repo.creatives.CREATIVE_SORTS), read
# off one ad instead of a footage group. Keys only: each is interpolated into the query.
AD_SORTS: dict[str, tuple[str, str | None]] = {
    "recent": ("x.first_seen_at", None),
    "spend": ("x.spend", None),
    "impressions": ("x.impressions", None),
    "purchases": ("x.purchases", None),
    "roas": ("x.conversion_value / NULLIF(x.spend, 0)", _CLICKED),
    "cpa": ("x.spend / NULLIF(x.conversions, 0)", _CLICKED),
    "link_ctr": ("x.link_clicks::numeric / NULLIF(x.impressions, 0)", _SEEN),
    "hook_rate": ("x.video_3s_views::numeric / NULLIF(x.impressions, 0)", _SEEN),
}


def _delivery_sql(delivery: str) -> str:
    if delivery not in DELIVERY:
        raise ValueError(f"bad delivery {delivery!r}")
    return {
        "spent": "x.impressions > 0",
        "live": "x.effective_status = 'ACTIVE'",
        "all": "TRUE",
    }[delivery]


def _ads_cte() -> str:
    """`x`: one row per ad in $1 org / $2 account, with its 30-day totals and its file's
    format. Filters and baselines below all read this one relation."""
    sums = ", ".join(f"COALESCE(m.{c}, 0) AS {c}" for c in METRIC_COLS)
    msums = ", ".join(f"sum({c}) AS {c}" for c in METRIC_COLS)
    return f"""
        WITH m AS (
            SELECT ad_id, {msums} FROM ad_metric_30d WHERE org_id = $1 GROUP BY ad_id
        ),
        x AS (
            SELECT ad.id, ad.ad_account_id, ad.platform_ad_id, ad.name, ad.status,
                   ad.effective_status, ad.first_seen_at,
                   COALESCE(ad.campaign_id, '') AS campaign_id, ad.campaign_name,
                   COALESCE(ad.adset_id, '') AS adset_id, ad.adset_name,
                   ad.creative_id, c.format, {sums}
            FROM ad JOIN creative c ON c.id = ad.creative_id
            LEFT JOIN m ON m.ad_id = ad.id
            WHERE ad.org_id = $1 AND ($2::uuid IS NULL OR ad.ad_account_id = $2)
        )
    """


async def tree(db: Database, org_id: UUID, ad_account_id: UUID | None) -> list[asyncpg.Record]:
    """One row per ad set, with its campaign and its 30-day totals. Small (org 3: 139 rows),
    so the page builds the campaign level and applies its filters itself."""
    sums = ", ".join(f"sum(x.{c}) AS {c}" for c in METRIC_COLS)
    return await db.fetch(
        f"""
        {_ads_cte()}
        SELECT x.ad_account_id, x.campaign_id, max(x.campaign_name) AS campaign_name,
               x.adset_id, max(x.adset_name) AS adset_name,
               count(*) AS ads,
               count(*) FILTER (WHERE x.effective_status = 'ACTIVE') AS ads_live,
               count(*) FILTER (WHERE x.impressions > 0) AS ads_spent,
               min(x.first_seen_at) AS first_seen_at, {sums}
        FROM x GROUP BY x.ad_account_id, x.campaign_id, x.adset_id
        """,
        org_id,
        ad_account_id,
    )


def _order_by(sort: str, order: str) -> str:
    if sort not in AD_SORTS or order not in ("asc", "desc"):
        raise ValueError(f"bad sort {sort!r}/{order!r}")
    expr, readable = AD_SORTS[sort]
    floor = f"({readable}) DESC, " if readable else ""
    return f"{floor}{expr} {order.upper()} NULLS LAST, x.id"


def _scope_sql() -> str:
    """The node being browsed: $3 campaign and $4 ad set, either or both may be NULL."""
    return "($3::text IS NULL OR x.campaign_id = $3) AND ($4::text IS NULL OR x.adset_id = $4)"


def _filter_sql(delivery: str) -> str:
    return (
        f"{_scope_sql()} AND {_delivery_sql(delivery)}"
        " AND ($5::text IS NULL OR x.format = $5)"
        " AND ($6::text IS NULL OR x.name ILIKE $6 OR x.adset_name ILIKE $6"
        " OR x.campaign_name ILIKE $6)"
    )


async def list_ads(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None,
    campaign_id: str | None,
    adset_id: str | None,
    format: str | None,
    q: str | None,
    delivery: str,
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> list[asyncpg.Record]:
    """The ads under one node, each with its own totals and the pooled totals of the other
    ads of its format in the same node (`base_*`). The comparison a buyer makes inside an ad
    set is against its siblings, which share its audience and budget; the account is the
    pool only at the top of the tree."""
    cols = ", ".join(f"x.{c}" for c in METRIC_COLS)
    base = ", ".join(f"p.{c} - x.{c} AS base_{c}" for c in METRIC_COLS)
    psums = ", ".join(f"sum({c}) AS {c}" for c in METRIC_COLS)
    return await db.fetch(
        f"""
        {_ads_cte()},
        p AS (
            SELECT ad_account_id, format, {psums} FROM x WHERE {_scope_sql()}
            GROUP BY ad_account_id, format
        )
        SELECT x.id, x.ad_account_id, x.platform_ad_id, x.name, x.status, x.effective_status,
               x.first_seen_at, x.campaign_id, x.campaign_name, x.adset_id, x.adset_name,
               x.creative_id, x.format, {cols}, {base},
               c.headline, c.primary_text, a.path AS primary_asset_path,
               a.mime AS primary_asset_mime,
               (SELECT t.path FROM asset t WHERE t.creative_id = c.id AND t.kind = 'thumbnail'
                ORDER BY t.seq LIMIT 1) AS poster_path
        FROM x
        JOIN p ON p.ad_account_id = x.ad_account_id AND p.format = x.format
        JOIN creative c ON c.id = x.creative_id
        LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE {_filter_sql(delivery)}
        ORDER BY {_order_by(sort, order)} LIMIT $7 OFFSET $8
        """,
        org_id,
        ad_account_id,
        campaign_id,
        adset_id,
        format,
        _search_pattern(q),
        limit,
        offset,
    )


async def count_ads(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None,
    campaign_id: str | None,
    adset_id: str | None,
    format: str | None,
    q: str | None,
) -> dict[str, int]:
    """How many ads each delivery filter would list here, so the page can say what the
    current one hides."""
    counts = ", ".join(
        f"count(*) FILTER (WHERE {_delivery_sql(d)}) AS {d}" for d in DELIVERY
    )
    r = await db.fetchrow(
        f"{_ads_cte()} SELECT {counts} FROM x WHERE {_filter_sql('all')}",
        org_id,
        ad_account_id,
        campaign_id,
        adset_id,
        format,
        _search_pattern(q),
    )
    return {d: int(r[d]) for d in DELIVERY}
