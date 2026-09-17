"""Compute and store one account read per ad account."""

from __future__ import annotations

import logging
import traceback
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights.account_read import repo
from cs.insights.account_read.patterns import run as run_patterns
from cs.insights.account_read.types import to_json
from cs.insights.account_read.units import build_units
from cs.insights.account_read.writer import GeminiGatewayAccountReadWriter, write_read
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.account_read")
JOB = "insights.compute_account_read"


def _default_writer(settings: Settings):
    return GeminiGatewayAccountReadWriter(settings)


WRITER_FACTORY = _default_writer


@handler(JOB)
async def compute_account_read(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    ad_account_id = UUID(ctx.job.payload["ad_account_id"])
    s = ctx.settings
    units, edges = await build_units(ctx.db, org_id, ad_account_id)
    if len(units) < s.account_read_min_units:
        await repo.insert_run(
            ctx.db,
            org_id,
            ad_account_id,
            status="insufficient",
            n_units=len(units),
            result={"summary": {"units": len(units)}},
            read=None,
            error=None,
        )
        return
    try:
        result = run_patterns(
            units,
            edges,
            grouping_dims=s.account_read_grouping_dims_list,
            draws=s.account_read_permutation_draws,
        )
        read, problems, ti, to = await write_read(result, WRITER_FACTORY(s))
    except Exception as exc:  # noqa: BLE001 - a failed run must not hide the previous one
        await repo.insert_run(
            ctx.db,
            org_id,
            ad_account_id,
            status="failed",
            n_units=len(units),
            result={"summary": {"units": len(units)}},
            read=None,
            error=f"{exc}\n{traceback.format_exc()}",
        )
        raise
    payload = to_json(result)  # facets serialize as sorted [dimension, value] pairs
    status = "done" if read else "rejected"
    await repo.insert_run(
        ctx.db,
        org_id,
        ad_account_id,
        status=status,
        n_units=len(units),
        result=payload,
        read=to_json(read) if read else None,
        error="; ".join(problems) or None,
        input_tokens=ti,
        output_tokens=to,
    )
    log.info(
        "account read stored",
        extra={
            "ad_account_id": str(ad_account_id),
            "status": status,
            "units": len(units),
            "tests": len(result.tests),
        },
    )


@register_schedule("insights.nightly_account_read", every_seconds=86400)
async def nightly_account_read(db: Database, settings: Settings) -> None:
    for a in await db.fetch("SELECT id, org_id FROM ad_account WHERE status='active'"):
        await queue.enqueue(
            db,
            a["org_id"],
            JOB,
            {"ad_account_id": str(a["id"])},
            dedup_key=f"account_read:{a['id']}",
            max_attempts=2,
        )
    n = await repo.purge_runs(db, 30)
    if n:
        log.info("purged account read runs", extra={"count": n})
