from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import fivetran_insights, fivetran_transform, repo, service
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.ingest")


@handler("meta.await_first_sync")
async def await_first_sync(ctx: JobContext) -> None:
    conn_id = UUID(ctx.job.payload["meta_connection_id"])
    conn = await repo.get_meta_connection(ctx.db, ctx.job.org_id)
    if conn is None or conn["id"] != conn_id:
        return  # connection was replaced by a newer connect attempt
    async with httpx.AsyncClient(timeout=30) as http:
        detail = await fivetran_client.get_connector(
            ctx.settings, http, conn["fivetran_connector_id"]
        )
    status = detail.get("status") or {}
    if status.get("setup_state") == "broken":
        await repo.set_meta_connection_status(
            ctx.db,
            conn_id,
            "error",
            last_error="Fivetran setup is broken -- reconnect from Settings.",
        )
        return
    if detail.get("succeeded_at"):
        await repo.set_meta_connection_status(ctx.db, conn_id, "ready")
        await queue.enqueue(
            ctx.db,
            ctx.job.org_id,
            "meta.sync_from_fivetran",
            {"meta_connection_id": str(conn_id)},
            dedup_key=str(conn_id),
        )
        return
    if ctx.job.attempts >= ctx.job.max_attempts:
        await repo.set_meta_connection_status(
            ctx.db,
            conn_id,
            "error",
            last_error=(
                "Fivetran's first sync did not finish in time -- "
                "check the connector in the Fivetran dashboard."
            ),
        )
        return
    raise RuntimeError("Fivetran has not completed its first sync yet")


@handler("meta.sync_from_fivetran")
async def sync_from_fivetran(ctx: JobContext) -> None:
    conn_id = UUID(ctx.job.payload["meta_connection_id"])
    conn = await repo.get_meta_connection(ctx.db, ctx.job.org_id)
    if conn is None or conn["id"] != conn_id:
        return
    try:
        entities = await fivetran_transform.discover_and_sync_entities(ctx, ctx.job.org_id, conn)
    except Exception as exc:
        await repo.set_meta_connection_status(ctx.db, conn_id, "error", last_error=str(exc)[:500])
        raise
    # Insights failing must not undo a successful entity sync, and must not be retried
    # forever: the most common cause is the ad_insights custom report not being
    # configured in Fivetran yet, which retrying does nothing to fix. Ads/creatives are
    # still fully usable without metrics, so the connection stays "ready" either way.
    try:
        metrics = await fivetran_insights.sync_insights(ctx, ctx.job.org_id, conn)
    except Exception as exc:
        metrics = {}
        log.warning(
            "insights sync failed (entity sync still succeeded) -- likely ad_insights "
            "custom report not yet configured in Fivetran",
            extra={"org_id": str(ctx.job.org_id), "error": str(exc)[:500]},
        )
    await repo.set_meta_connection_status(
        ctx.db, conn_id, "ready", last_synced_at=datetime.now(UTC)
    )
    log.info("fivetran sync complete", extra={**entities, **metrics, "org_id": str(ctx.job.org_id)})


@handler("meta.download_creative")
async def download_creative(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await service.download_creative(ctx, http, creative_id, p["asset_url"], p["kind"])
    log.info("creative downloaded", extra={"creative_id": str(creative_id)})


@handler("meta.download_carousel")
async def download_carousel(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await service.download_carousel(ctx, http, creative_id, p["asset_urls"])
    log.info(
        "carousel downloaded",
        extra={"creative_id": str(creative_id), "cards": len(p["asset_urls"])},
    )


@register_schedule("ingest.nightly_sync", every_seconds=86400)
async def nightly_sync(db: Database, settings: Settings) -> None:
    for c in await repo.list_ready_meta_connections_with_active_account(db):
        await queue.enqueue(
            db,
            c["org_id"],
            "meta.sync_from_fivetran",
            {"meta_connection_id": str(c["id"])},
            dedup_key=str(c["id"]),
        )
