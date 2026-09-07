from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow
from cs.ingest import repo
from cs.jobs.registry import JobContext

# Ported from the deleted cs.connectors.meta.parse.CONVERSION_ACTION_TYPES -- the business
# logic (which action_type counts as a conversion) is unchanged; only the source shape is.
CONVERSION_ACTION_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
    "onsite_conversion.lead_grouped",
)


def _q(schema: str, table: str) -> str:
    return f'"{schema}".{table}'


async def _action_totals(db, schema: str, table: str) -> dict[tuple[str, date], dict[str, Decimal]]:
    """(ad_id, date) -> {action_type: value}, summed across `index` (Meta can report the
    same action_type more than once per breakdown row)."""
    rows = await db.fetch(f"""
        SELECT ad_id, date, action_type, sum(value) AS total
        FROM {_q(schema, table)} GROUP BY ad_id, date, action_type
    """)
    out: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in rows:
        out.setdefault((r["ad_id"], r["date"]), {})[r["action_type"]] = Decimal(str(r["total"]))
    return out


def _first_matching(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> Decimal:
    if not by_type:
        return Decimal(0)
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _video_total(by_type: dict[str, Decimal] | None) -> int:
    return int(sum((by_type or {}).values()))


async def sync_insights(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    actions = await _action_totals(db, schema, "ad_insights_actions")
    action_values = await _action_totals(db, schema, "ad_insights_action_values")
    video_3s = await _action_totals(db, schema, "ad_insights_video_play_actions")
    video_thruplay = await _action_totals(db, schema, "ad_insights_video_thruplay_watched_actions")
    video_p50 = await _action_totals(db, schema, "ad_insights_video_p50_watched_actions")
    video_p100 = await _action_totals(db, schema, "ad_insights_video_p100_watched_actions")

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, 'ad_insights')}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (r["ad_id"], r["date"])
        publisher = r["publisher_platform"] or "unknown"
        position = r["platform_position"] or "unknown"
        metric_rows.append(
            MetricRow(
                platform_ad_id=r["ad_id"],
                day=r["date"],
                placement=f"{publisher}:{position}",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=int(r["clicks"] or 0),
                link_clicks=int(r["inline_link_clicks"] or 0),
                conversions=_first_matching(actions.get(key), CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(action_values.get(key), CONVERSION_ACTION_TYPES),
                video_3s_views=_video_total(video_3s.get(key)),
                video_thruplay=_video_total(video_thruplay.get(key)),
                video_p50=_video_total(video_p50.get(key)),
                video_p100=_video_total(video_p100.get(key)),
                raw=dict(r),
            )
        )

    # metric_daily is keyed per ad_account_id, but ad_insights doesn't carry one --
    # upsert_metrics resolves platform_ad_id -> (ad_account_id, creative_id) via the `ad`
    # table itself, so every ad in this org's insights is grouped by its actual account.
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
    # Ads whose account we couldn't resolve at all (not in `by_account`) count as skipped too.
    skipped += len(metric_rows) - sum(len(v) for v in by_account.values())
    return {"written": written, "skipped": skipped}
