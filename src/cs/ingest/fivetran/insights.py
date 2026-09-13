from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow
from cs.core import repo
from cs.ingest.fivetran.schema import (
    BASIC_AD,
    BASIC_AD_ACTION_VALUES,
    BASIC_AD_ACTIONS,
    VIDEO_RETENTION_AD,
    VIDEO_RETENTION_CHILD_COLUMNS,
)
from cs.jobs.registry import JobContext

CONVERSION_ACTION_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
    "onsite_conversion.lead_grouped",
)

# Meta's 3-second video view, the hook-rate numerator. Lands in basic_ad_actions like any
# other action_type. ThruPlay and p25-p100 are separate Graph API fields that only reach
# us through the video_retention_ad custom report (see _retention_totals).
VIDEO_3S_ACTION_TYPES = ("video_view",)

# Step-conversion and engagement action_types. First match wins; omni_* is preferred
# because Meta dedups it across web/app/onsite channels.
LANDING_PAGE_VIEW_TYPES = ("omni_landing_page_view", "landing_page_view")
CONTENT_VIEW_TYPES = (
    "omni_view_content",
    "view_content",
    "offsite_conversion.fb_pixel_view_content",
)
ADD_TO_CART_TYPES = ("omni_add_to_cart", "add_to_cart", "offsite_conversion.fb_pixel_add_to_cart")
CHECKOUT_TYPES = (
    "omni_initiated_checkout",
    "initiate_checkout",
    "offsite_conversion.fb_pixel_initiate_checkout",
)
PURCHASE_TYPES = ("omni_purchase", "purchase", "offsite_conversion.fb_pixel_purchase")
POST_REACTION_TYPES = ("post_reaction",)
COMMENT_TYPES = ("comment",)
SAVE_TYPES = ("onsite_conversion.post_save",)


def _q(schema: str, table: str) -> str:
    return f'"{schema}".{table}'


def _pid(value: object) -> str | None:
    return None if value is None else str(value)


async def _action_totals(db, schema: str, table: str) -> dict[tuple[str, date], dict[str, Decimal]]:
    rows = await db.fetch(f"""
        SELECT ad_id, date, action_type, sum(value) AS total
        FROM {_q(schema, table)} GROUP BY ad_id, date, action_type
    """)
    out: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in rows:
        bucket = out.setdefault((_pid(r["ad_id"]), r["date"]), {})
        bucket[r["action_type"]] = Decimal(str(r["total"]))
    return out


async def _table_exists(db, schema: str, table: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=$1 AND table_name=$2",
            schema,
            table,
        )
    )


async def _column_exists(db, schema: str, table: str, column: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=$1 AND table_name=$2 AND column_name=$3",
            schema,
            table,
            column,
        )
    )


async def _retention_totals(db, schema: str) -> dict[tuple[str, date], dict[str, float]] | None:
    """Per (ad_id, day) totals from the video_retention_ad child tables, or None when the
    custom report has not been configured for this connector (org 1 today)."""
    if not await _table_exists(db, schema, VIDEO_RETENTION_AD):
        return None
    out: dict[tuple[str, date], dict[str, float]] = {}
    for suffix, column in VIDEO_RETENTION_CHILD_COLUMNS.items():
        table = f"{VIDEO_RETENTION_AD}_{suffix}"
        if not await _table_exists(db, schema, table):
            continue
        rows = await db.fetch(f"""
            SELECT ad_id, date, sum(value) AS total FROM {_q(schema, table)}
            WHERE action_type = 'video_view' GROUP BY ad_id, date
        """)
        for r in rows:
            out.setdefault((_pid(r["ad_id"]), r["date"]), {})[column] = float(r["total"] or 0)
    return out


def _first_matching(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> Decimal:
    if not by_type:
        return Decimal(0)
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _count(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> int:
    return int(_first_matching(by_type, types))


async def sync_insights(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    actions = await _action_totals(db, schema, BASIC_AD_ACTIONS)
    action_values = await _action_totals(db, schema, BASIC_AD_ACTION_VALUES)
    retention = await _retention_totals(db, schema)
    has_reach = await _column_exists(db, schema, BASIC_AD, "reach")

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, BASIC_AD)}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (_pid(r["ad_id"]), r["date"])
        link_clicks = int(r["inline_link_clicks"] or 0)
        acts = actions.get(key)
        ret = (retention or {}).get(key, {})
        plays = int(ret.get("video_plays", 0))
        metric_rows.append(
            MetricRow(
                platform_ad_id=_pid(r["ad_id"]),
                day=r["date"],
                placement="all",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=link_clicks,
                link_clicks=link_clicks,
                conversions=_first_matching(acts, CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(action_values.get(key), CONVERSION_ACTION_TYPES),
                video_3s_views=_count(acts, VIDEO_3S_ACTION_TYPES),
                video_thruplay=int(ret.get("video_thruplay", 0)),
                video_p50=int(ret.get("video_p50", 0)),
                video_p100=int(ret.get("video_p100", 0)),
                raw=dict(r),
                reach=int(r["reach"] or 0) if has_reach else 0,
                video_plays=plays,
                video_p25=int(ret.get("video_p25", 0)),
                video_p75=int(ret.get("video_p75", 0)),
                video_p95=int(ret.get("video_p95", 0)),
                # avg_time_watched is a per-day mean in seconds; multiply by plays so the
                # column sums across days (avg = sum(seconds) / sum(plays) downstream).
                video_watch_seconds=Decimal(
                    str(round(ret.get("video_avg_time_watched", 0.0) * plays, 4))
                ),
                landing_page_views=_count(acts, LANDING_PAGE_VIEW_TYPES),
                content_views=_count(acts, CONTENT_VIEW_TYPES),
                add_to_carts=_count(acts, ADD_TO_CART_TYPES),
                checkouts=_count(acts, CHECKOUT_TYPES),
                purchases=_count(acts, PURCHASE_TYPES),
                post_reactions=_count(acts, POST_REACTION_TYPES),
                comments=_count(acts, COMMENT_TYPES),
                saves=_count(acts, SAVE_TYPES),
            )
        )

    by_account: dict[UUID, list[MetricRow]] = {}
    ad_rows = await db.fetch(
        "SELECT id, ad_account_id, platform_ad_id FROM ad WHERE org_id=$1", org_id
    )
    account_by_platform_ad_id = {a["platform_ad_id"]: a["ad_account_id"] for a in ad_rows}
    for m in metric_rows:
        acct_id = account_by_platform_ad_id.get(m.platform_ad_id)
        if acct_id is not None:
            by_account.setdefault(acct_id, []).append(m)

    written = skipped = 0
    for acct_id, rows in by_account.items():
        w, s = await repo.upsert_metrics(db, org_id, acct_id, rows)
        written += w
        skipped += s
    skipped += len(metric_rows) - sum(len(v) for v in by_account.values())
    return {"written": written, "skipped": skipped, "retention_tables": retention is not None}
