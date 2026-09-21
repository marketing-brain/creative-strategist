from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformCreative
from cs.core.db import Database
from cs.core.ids import new_id
from cs.core.repo.footage import FOOTAGE_KEY_SQL


async def upsert_creative(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    c: PlatformCreative,
    raw_path: str,
    *,
    format_source: str,
) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline,
                              primary_text, description, cta_type, link_url, raw_path,
                              format_source, format_verified_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                CASE WHEN $12 = 'unresolved' THEN NULL ELSE now() END)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET
          format = EXCLUDED.format,
          headline = EXCLUDED.headline,
          primary_text = EXCLUDED.primary_text,
          description = EXCLUDED.description,
          cta_type = EXCLUDED.cta_type,
          link_url = EXCLUDED.link_url,
          format_source = EXCLUDED.format_source,
          format_verified_at = EXCLUDED.format_verified_at,
          last_seen_at = now()
        RETURNING id, (xmax = 0) AS created
        """,
        new_id(),
        org_id,
        ad_account_id,
        c.platform_creative_id,
        c.format,
        c.headline,
        c.primary_text,
        c.description,
        c.cta_type,
        c.link_url,
        raw_path,
        format_source,
    )
    return row["id"], row["created"]


async def set_creative_format(
    db: Database, org_id: UUID, creative_id: UUID, format: str, format_source: str
) -> None:
    await db.execute(
        "UPDATE creative SET format=$3, format_source=$4,"
        " format_verified_at = CASE WHEN $4 = 'unresolved' THEN NULL ELSE now() END"
        " WHERE org_id=$1 AND id=$2",
        org_id,
        creative_id,
        format,
        format_source,
    )


async def primary_asset_kind(
    db: Database, ad_account_id: UUID, platform_creative_id: str
) -> str | None:
    """Kind of the stored creative's primary file, or None (no file, or not stored yet)."""
    return await db.fetchval(
        "SELECT a.kind FROM creative c JOIN asset a ON a.id = c.primary_asset_id"
        " WHERE c.ad_account_id=$1 AND c.platform_creative_id=$2",
        ad_account_id,
        platform_creative_id,
    )


def _ids(creative: UUID | list[UUID]) -> list[UUID]:
    """Metric readers take one creative or a footage group; both run as `= ANY(ids)`."""
    return list(creative) if isinstance(creative, list) else [creative]


def _tag_match_sql(dim_param: str, value_param: str) -> str:
    """True when no tag filter is set, or the creative's latest done run carries the tag."""
    return f"""
      ({dim_param}::text IS NULL OR EXISTS (
            SELECT 1 FROM tag t
            WHERE t.tag_run_id = (
                SELECT id FROM tag_run tr
                WHERE tr.creative_id = c.id AND tr.status = 'done'
                ORDER BY tr.finished_at DESC LIMIT 1
            ) AND t.dimension = {dim_param} AND t.value = {value_param}
          ))
"""


# Floors under which a rate is noise, the same ones the creative page compares with: 1,000
# impressions for attention rates, 100 link clicks for anything about purchases.
IMPRESSION_FLOOR = 1000
CLICK_FLOOR = 100

# Sort keys the API accepts, mapped to (expression, readable-when). The caller must pass a
# key from this dict: it is interpolated into the query, so it can never be user text.
# The expressions read the group row `g`, so a footage group sorts on its pooled totals.
# A rate sort carries the floor it needs: groups under it sort after every readable group in
# both directions, or "best ROAS" would be led by a four-dollar ad with one lucky purchase.
_SEEN = f"g.impressions >= {IMPRESSION_FLOOR}"
_CLICKED = f"({_SEEN} AND g.link_clicks >= {CLICK_FLOOR})"
CREATIVE_SORTS: dict[str, tuple[str, str | None]] = {
    "recent": ("g.first_seen_at", None),
    "spend": ("g.spend", None),
    "impressions": ("g.impressions", None),
    "purchases": ("g.purchases", None),
    "roas": ("g.conversion_value / NULLIF(g.spend, 0)", _CLICKED),
    "cpa": ("g.spend / NULLIF(g.conversions, 0)", _CLICKED),
    "link_ctr": ("g.link_clicks::numeric / NULLIF(g.impressions, 0)", _SEEN),
    "hook_rate": ("g.video_3s_views::numeric / NULLIF(g.impressions, 0)", _SEEN),
    # Kept for old links: all-clicks CTR, which the grid no longer shows.
    "ctr": ("g.clicks::numeric / NULLIF(g.impressions, 0)", _SEEN),
}
CREATIVE_ORDERS = ("desc", "asc")
CREATIVE_GROUPS = ("footage", "creative")
CREATIVE_DELIVERY = ("active", "all")


def _order_by(sort: str, order: str) -> str:
    """ORDER BY for the creative list.

    NULLS LAST in both directions: a creative with no impressions has no CTR, and ascending
    CTR should mean "worst real CTR first", not "everything that never ran first". Rate
    sorts also put groups under their floor last in both directions.

    The representative's id breaks ties so paging is stable. Zero-spend creatives all tie at
    0, and without a unique tiebreak Postgres may order a tied group differently per query,
    which duplicates and skips rows across pages.
    """
    if sort not in CREATIVE_SORTS or order not in CREATIVE_ORDERS:
        raise ValueError(f"bad sort {sort!r}/{order!r}")
    expr, readable = CREATIVE_SORTS[sort]
    floor = f"({readable}) DESC, " if readable else ""
    return f"{floor}{expr} {order.upper()} NULLS LAST, rep.id"


def _group_key(group: str) -> str:
    if group not in CREATIVE_GROUPS:
        raise ValueError(f"bad group {group!r}")
    return FOOTAGE_KEY_SQL if group == "footage" else "c.id::text"


GRID_METRIC_COLS = (
    "spend",
    "impressions",
    "clicks",
    "link_clicks",
    "video_3s_views",
    "purchases",
    "conversions",
    "conversion_value",
)


def _grouped_cte(group: str, dim_param: str, value_param: str, search_param: str) -> str:
    """`base` is one row per creative passing the account and format filters, `g` one row per
    group with pooled 30-day totals, `rep` the group's highest-spend creative, which lends
    the card its copy, preview, ad name and link. Filters on $1 org, $2 account, $3 format.

    The tag filter and the search are judged per group, not per creative: a group matches
    when any of its creatives does, and its totals still cover every member. Filtering
    members first would shrink a group's spend to whichever copies happen to match.

    `acct` is the account's pooled totals per format. A card compares its group with the
    rest of that pool (pool minus the group), the same baseline the creative page uses, so
    a number that reads below the account on the grid reads the same once opened."""
    sums = ", ".join(f"COALESCE(m.{c}, 0) AS {c}" for c in GRID_METRIC_COLS)
    msums = ", ".join(f"sum({c}) AS {c}" for c in GRID_METRIC_COLS)
    # Thirty-day totals come from the rollups (cs.core.repo.rollups), never metric_daily:
    # this CTE runs for every grid page and again for its count.
    mcols = ", ".join(GRID_METRIC_COLS)
    return f"""
        WITH ad_spend AS (
            SELECT ad_id, sum(spend) AS spend FROM ad_metric_30d
            WHERE org_id = $1 GROUP BY ad_id
        ),
        base AS (
            SELECT c.id, c.ad_account_id, c.platform_creative_id, c.format, c.headline,
                   c.primary_text, c.cta_type, c.link_url, c.first_seen_at, c.last_seen_at,
                   a.path AS primary_asset_path, a.mime AS primary_asset_mime,
                   {_group_key(group)} AS gkey,
                   {_tag_match_sql(dim_param, value_param)} AS tag_match,
                   ({search_param}::text IS NULL
                    OR c.headline ILIKE {search_param} OR c.primary_text ILIKE {search_param}
                    OR c.platform_creative_id ILIKE {search_param}
                    OR COALESCE(n.name_match, false)) AS search_match,
                   {sums}, COALESCE(n.ads, 0) AS ads, n.ad_name
            FROM creative c
            LEFT JOIN asset a ON a.id = c.primary_asset_id
            LEFT JOIN (
                SELECT creative_id, {mcols} FROM creative_metric_30d WHERE org_id = $1
            ) m ON m.creative_id = c.id
            LEFT JOIN (
                SELECT ad.creative_id, count(*) AS ads,
                       bool_or(ad.name ILIKE {search_param}) AS name_match,
                       (array_agg(ad.name ORDER BY COALESCE(s.spend, 0) DESC,
                                  ad.first_seen_at, ad.id))[1] AS ad_name
                FROM ad LEFT JOIN ad_spend s ON s.ad_id = ad.id
                WHERE ad.org_id = $1 GROUP BY ad.creative_id
            ) n ON n.creative_id = c.id
            WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
              AND ($3::text IS NULL OR c.format = $3)
        ),
        g AS (
            SELECT gkey, count(*) AS creative_count, sum(ads) AS ad_count,
                   count(DISTINCT (COALESCE(headline, ''), COALESCE(primary_text, '')))
                       AS copy_variants,
                   min(first_seen_at) AS first_seen_at, max(last_seen_at) AS last_seen_at,
                   {msums}
            FROM base GROUP BY gkey HAVING bool_or(tag_match) AND bool_or(search_match)
        ),
        acct AS (
            SELECT ad_account_id, format, {msums} FROM base GROUP BY ad_account_id, format
        ),
        rep AS (
            SELECT DISTINCT ON (gkey) * FROM base ORDER BY gkey, spend DESC, first_seen_at, id
        )
"""


def _search_pattern(q: str | None) -> str | None:
    """ILIKE pattern for a free-text search, with the user's own % and _ made literal."""
    q = (q or "").strip()
    if not q:
        return None
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _delivery_sql(delivery: str) -> str:
    if delivery not in CREATIVE_DELIVERY:
        raise ValueError(f"bad delivery {delivery!r}")
    return "g.impressions > 0" if delivery == "active" else "TRUE"


async def list_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    limit: int = 50,
    offset: int = 0,
    dimension: str | None = None,
    value: str | None = None,
    sort: str = "recent",
    order: str = "desc",
    group: str = "creative",
    delivery: str = "all",
    q: str | None = None,
) -> list[asyncpg.Record]:
    """One row per creative, or with group='footage' one row per footage group (see
    cs.core.repo.footage) carrying the group's pooled 30-day totals, the pooled totals of
    the rest of its account and format (`base_*`), and a poster image path when one exists.
    delivery='active' leaves out groups with no impressions in the window."""
    gcols = ", ".join(f"g.{c} AS {c}_30d" for c in GRID_METRIC_COLS)
    bcols = ", ".join(f"acct.{c} - g.{c} AS base_{c}" for c in GRID_METRIC_COLS)
    return await db.fetch(
        f"""
        {_grouped_cte(group, "$6", "$7", "$8")}
        SELECT rep.id, rep.ad_account_id, rep.platform_creative_id, rep.format, rep.headline,
               rep.primary_text, rep.cta_type, rep.link_url, g.first_seen_at, g.last_seen_at,
               rep.primary_asset_path, rep.primary_asset_mime, rep.ad_name,
               (SELECT t.path FROM asset t WHERE t.creative_id = rep.id AND t.kind = 'thumbnail'
                ORDER BY t.seq LIMIT 1) AS poster_path,
               g.creative_count, g.ad_count, g.copy_variants, {gcols}, {bcols}
        FROM g JOIN rep USING (gkey)
        JOIN acct ON acct.ad_account_id = rep.ad_account_id AND acct.format = rep.format
        WHERE {_delivery_sql(delivery)}
        ORDER BY {_order_by(sort, order)} LIMIT $4 OFFSET $5
        """,
        org_id,
        ad_account_id,
        format,
        limit,
        offset,
        dimension,
        value,
        _search_pattern(q),
    )


async def creative_counts(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    group: str = "creative",
    q: str | None = None,
) -> tuple[int, int]:
    """(groups matching the filters, how many of them delivered in the last 30 days)."""
    r = await db.fetchrow(
        f"""
        {_grouped_cte(group, "$4", "$5", "$6")}
        SELECT count(*) AS total, count(*) FILTER (WHERE g.impressions > 0) AS active FROM g
        """,
        org_id,
        ad_account_id,
        format,
        dimension,
        value,
        _search_pattern(q),
    )
    return int(r["total"]), int(r["active"])


async def count_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    group: str = "creative",
) -> int:
    total, _ = await creative_counts(db, org_id, ad_account_id, format, dimension, value, group)
    return total


async def get_creative(db: Database, org_id: UUID, creative_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime,
               a.duration_ms AS primary_asset_duration_ms, a.sha256 AS primary_asset_sha256
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND c.id = $2
        """,
        org_id,
        creative_id,
    )


async def creative_metrics(
    db: Database, org_id: UUID, creative_id: UUID | list[UUID], since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT day, sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
               sum(link_clicks) AS link_clicks, sum(conversions) AS conversions,
               sum(conversion_value) AS conversion_value, sum(video_3s_views) AS video_3s_views,
               sum(video_thruplay) AS video_thruplay
        FROM metric_daily WHERE org_id=$1 AND creative_id = ANY($2) AND day BETWEEN $3 AND $4
        GROUP BY day ORDER BY day
        """,
        org_id,
        _ids(creative_id),
        since,
        until,
    )


_SUMS_SQL = """
    sum(impressions) AS impressions, sum(reach) AS reach, sum(link_clicks) AS link_clicks,
    sum(spend) AS spend, sum(video_3s_views) AS video_3s_views, sum(video_plays) AS video_plays,
    sum(video_p25) AS video_p25, sum(video_p50) AS video_p50, sum(video_p75) AS video_p75,
    sum(video_p95) AS video_p95, sum(video_p100) AS video_p100,
    sum(video_thruplay) AS video_thruplay, sum(video_watch_seconds) AS video_watch_seconds,
    sum(landing_page_views) AS landing_page_views, sum(content_views) AS content_views,
    sum(add_to_carts) AS add_to_carts, sum(checkouts) AS checkouts, sum(purchases) AS purchases,
    sum(post_reactions) AS post_reactions, sum(comments) AS comments, sum(saves) AS saves,
    sum(conversions) AS conversions, sum(conversion_value) AS conversion_value
"""


async def creative_window_sums(
    db: Database, org_id: UUID, creative_id: UUID | list[UUID], since: date, until: date
) -> asyncpg.Record:
    """Always returns one row; every sum is NULL when no rows fall in the window."""
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL} FROM metric_daily "
        "WHERE org_id=$1 AND creative_id = ANY($2) AND day BETWEEN $3 AND $4",
        org_id,
        _ids(creative_id),
        since,
        until,
    )


async def baseline_window_sums(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    fmt: str,
    exclude_creative_id: UUID | list[UUID],
    since: date,
    until: date,
    duration_range_ms: tuple[int, int | None] | None = None,
) -> asyncpg.Record:
    """Pooled sums over every *other* creative of the same format in the account. With
    `duration_range_ms` (low exclusive, high inclusive, None = open), only creatives whose
    primary asset length falls in that range; creatives of unknown length are left out.
    `creatives` counts distinct files, not creative rows: eleven copies of one video are one
    peer, which is what the panel means by "other videos"."""
    lo, hi = duration_range_ms if duration_range_ms is not None else (None, None)
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL}, "
        "count(DISTINCT COALESCE(a.sha256, m.creative_id::text)) AS creatives "
        "FROM metric_daily m JOIN creative c ON c.id = m.creative_id "
        "LEFT JOIN asset a ON a.id = c.primary_asset_id "
        "WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3 AND m.creative_id <> ALL($4) "
        "AND m.day BETWEEN $5 AND $6 "
        "AND ($7::bigint IS NULL OR a.duration_ms > $7) "
        "AND ($8::bigint IS NULL OR a.duration_ms <= $8)",
        org_id,
        ad_account_id,
        fmt,
        _ids(exclude_creative_id),
        since,
        until,
        lo,
        hi,
    )


SIBLING_SCOPES = ("ad set", "campaign", "account")
MIN_SIBLINGS = 3


async def sibling_creative_ids(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    creative_id: UUID | list[UUID],
    fmt: str,
    since: date,
    until: date,
    scope: str,
) -> list[UUID]:
    """Creatives of the same format to measure this one against, within one scope.

    Meta's optimiser, not the advertiser, decides a creative's placement mix and audience, so
    the comparison has to hold targeting fixed. Comparing against the whole account mixes in
    campaigns with their own age or gender caps, which shows up as a demographic skew that
    belongs to the campaign rather than to the creative. Given a footage group, its own
    members are never its peers: they are the same file. The caller walks the scopes from
    narrowest out (see SIBLING_SCOPES) and takes the first with enough delivery to compare.

    Peers only count when they have audience rows in the window; otherwise a narrow scope
    would win on paper and contribute nothing to compare with."""
    if scope == "account":
        return await _peers_in_account(db, org_id, ad_account_id, creative_id, fmt, since, until)
    column = "adset_id" if scope == "ad set" else "campaign_id"
    return await _peers_sharing(db, org_id, creative_id, fmt, since, until, column)


# Audience rows are monthly; any month touching the window counts, on both sides.
def _month_overlap(since: int, until: int) -> str:
    return f"a.month <= ${until} AND (a.month + INTERVAL '1 month' - INTERVAL '1 day') >= ${since}"


_HAS_AUDIENCE_ROWS = f"""
    EXISTS (SELECT 1 FROM metric_audience a
            WHERE a.creative_id = c.id AND {_month_overlap(4, 5)})
"""


async def _peers_sharing(
    db: Database,
    org_id: UUID,
    creative_id: UUID | list[UUID],
    fmt: str,
    since: date,
    until: date,
    column: str,
) -> list[UUID]:
    """Other creatives of the same format that ran in an ad set (or campaign) this one ran in."""
    rows = await db.fetch(
        f"""
        SELECT DISTINCT c.id FROM ad mine
        JOIN ad peer ON peer.org_id = mine.org_id AND peer.{column} = mine.{column}
        JOIN creative c ON c.id = peer.creative_id
        WHERE mine.org_id = $1 AND mine.creative_id = ANY($2) AND mine.{column} IS NOT NULL
          AND c.id <> ALL($2) AND c.format = $3 AND {_HAS_AUDIENCE_ROWS}
        """,
        org_id,
        _ids(creative_id),
        fmt,
        since,
        until,
    )
    return [r["id"] for r in rows]


async def _peers_in_account(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    creative_id: UUID | list[UUID],
    fmt: str,
    since: date,
    until: date,
) -> list[UUID]:
    rows = await db.fetch(
        f"""
        SELECT c.id FROM creative c
        WHERE c.org_id = $1 AND c.ad_account_id = $6 AND c.id <> ALL($2) AND c.format = $3
          AND {_HAS_AUDIENCE_ROWS}
        """,
        org_id,
        _ids(creative_id),
        fmt,
        since,
        until,
        ad_account_id,
    )
    return [r["id"] for r in rows]


async def creative_audience_sums(
    db: Database, org_id: UUID, creative_id: UUID | list[UUID], since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT a.age, a.gender, sum(a.impressions) AS impressions FROM metric_audience a "
        f"WHERE a.org_id=$1 AND a.creative_id = ANY($2) AND {_month_overlap(3, 4)} GROUP BY 1, 2",
        org_id,
        _ids(creative_id),
        since,
        until,
    )


async def sibling_audience_sums(
    db: Database, org_id: UUID, creative_ids: list[UUID], since: date, until: date
) -> list[asyncpg.Record]:
    if not creative_ids:
        return []
    return await db.fetch(
        "SELECT a.age, a.gender, sum(a.impressions) AS impressions FROM metric_audience a "
        f"WHERE a.org_id=$1 AND a.creative_id = ANY($2) AND {_month_overlap(3, 4)} GROUP BY 1, 2",
        org_id,
        creative_ids,
        since,
        until,
    )


async def creative_first_metric_day(
    db: Database, org_id: UUID, creative_id: UUID | list[UUID]
) -> date | None:
    return await db.fetchval(
        "SELECT min(day) FROM metric_daily "
        "WHERE org_id=$1 AND creative_id = ANY($2) AND impressions > 0",
        org_id,
        _ids(creative_id),
    )


async def account_last_metric_day(db: Database, org_id: UUID, ad_account_id: UUID) -> date | None:
    """The newest day the account has delivery for.

    Every window on the creative page ends here rather than on the wall clock. Meta reports
    through Fivetran on its own schedule, and a connector that is late, paused or mid-sync
    leaves the last few days empty. Anchored on today, the fatigue tier's trailing 7 days
    can hold one partial day of delivery and fall under its impression floor while the
    creative is in fact running normally."""
    return await db.fetchval(
        "SELECT max(day) FROM metric_daily "
        "WHERE org_id=$1 AND ad_account_id=$2 AND impressions > 0",
        org_id,
        ad_account_id,
    )
