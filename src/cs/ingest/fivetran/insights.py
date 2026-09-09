from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow
from cs.core import repo
from cs.ingest.fivetran.schema import BASIC_AD, BASIC_AD_ACTION_VALUES, BASIC_AD_ACTIONS
from cs.jobs.registry import JobContext

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


def _first_matching(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> Decimal:
    if not by_type:
        return Decimal(0)
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


async def sync_insights(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    actions = await _action_totals(db, schema, BASIC_AD_ACTIONS)
    action_values = await _action_totals(db, schema, BASIC_AD_ACTION_VALUES)

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, BASIC_AD)}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (_pid(r["ad_id"]), r["date"])
        link_clicks = int(r["inline_link_clicks"] or 0)
        metric_rows.append(
            MetricRow(
                platform_ad_id=_pid(r["ad_id"]),
                day=r["date"],
                placement="all",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=link_clicks,
                link_clicks=link_clicks,
                conversions=_first_matching(actions.get(key), CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(action_values.get(key), CONVERSION_ACTION_TYPES),
                video_3s_views=0,
                video_thruplay=0,
                video_p50=0,
                video_p100=0,
                raw=dict(r),
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
    return {"written": written, "skipped": skipped}
