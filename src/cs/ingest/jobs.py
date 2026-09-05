from __future__ import annotations

import logging
from uuid import UUID

import httpx

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import repo, service
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.ingest")


async def _account(ctx: JobContext):
    acct = await repo.get_ad_account(ctx.db, ctx.job.org_id, UUID(ctx.job.payload["ad_account_id"]))
    if acct is None:
        raise RuntimeError("ad_account not found")
    return acct


@handler("meta.backfill")
async def backfill(ctx: JobContext) -> None:
    acct = await _account(ctx)
    since, until = service.window(service.BACKFILL_DAYS)
    async with httpx.AsyncClient(timeout=60) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        result = await service.sync_account(ctx, acct, connector, since, until)
    log.info("sync complete", extra={**result, "ad_account_id": str(acct["id"])})


@handler("meta.sync_incremental")
async def sync_incremental(ctx: JobContext) -> None:
    acct = await _account(ctx)
    since, until = service.window(service.INCREMENTAL_DAYS)
    async with httpx.AsyncClient(timeout=60) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        result = await service.sync_account(ctx, acct, connector, since, until)
    log.info("sync complete", extra={**result, "ad_account_id": str(acct["id"])})


@handler("meta.download_creative")
async def download_creative(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    acct = await ctx.db.fetchrow(
        "SELECT a.* FROM ad_account a JOIN creative c ON c.ad_account_id = a.id "
        "WHERE c.id = $1 AND c.org_id = $2",
        creative_id,
        ctx.job.org_id,
    )
    if acct is None:
        raise RuntimeError("ad_account not found for creative")
    async with httpx.AsyncClient(timeout=120) as http:
        connector = service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
        await service.download_creative(ctx, connector, creative_id, p["asset_url"], p["kind"])
    log.info(
        "creative downloaded",
        extra={"creative_id": str(creative_id), "ad_account_id": str(acct["id"])},
    )


@register_schedule("ingest.nightly_sync", every_seconds=86400)
async def nightly_sync(db: Database, settings: Settings) -> None:
    accounts = await db.fetch("SELECT id, org_id FROM ad_account WHERE status = 'active'")
    for a in accounts:
        await queue.enqueue(
            db,
            a["org_id"],
            "meta.sync_incremental",
            {"ad_account_id": str(a["id"])},
            dedup_key=str(a["id"]),
        )
