from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core import queue, repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.repo import rollups
from cs.ingest import assets, facebook_video, instagram_scrape
from cs.ingest.fivetran import insights, transform
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.ingest")


def _verdict(payload: dict) -> tuple[str, str] | None:
    """The label a queued download stores once its file lands (absent on older jobs)."""
    if payload.get("format") and payload.get("format_source"):
        return payload["format"], payload["format_source"]
    return None


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
        if status.get("setup_state") == "connected" and detail.get("paused"):
            await fivetran_client.lock_down_and_activate(
                ctx.settings, http, conn["fivetran_connector_id"], conn["schema_name"]
            )
        elif detail.get("succeeded_at"):
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
        entities = await transform.discover_and_sync_entities(ctx, ctx.job.org_id, conn)
    except Exception as exc:
        await repo.set_meta_connection_status(ctx.db, conn_id, "error", last_error=str(exc)[:500])
        raise
    # Insights failing must not undo a successful entity sync, and must not be retried
    # forever: the most common cause is the ad_insights custom report not being
    # configured in Fivetran yet, which retrying does nothing to fix. Ads/creatives are
    # still fully usable without metrics, so the connection stays "ready" either way.
    try:
        metrics = await insights.sync_insights(ctx, ctx.job.org_id, conn)
    except Exception as exc:
        metrics = {}
        log.warning(
            "insights sync failed (entity sync still succeeded) -- likely ad_insights "
            "custom report not yet configured in Fivetran",
            extra={"org_id": str(ctx.job.org_id), "error": str(exc)[:500]},
        )
    # The grid reads thirty-day totals from the rollups, so new metrics show only after this.
    # A failed refresh leaves the last totals in place until the next sync refreshes them.
    try:
        await rollups.refresh(ctx.db)
    except Exception:  # noqa: BLE001
        log.exception("metric rollup refresh failed", extra={"org_id": str(ctx.job.org_id)})
    await repo.set_meta_connection_status(
        ctx.db, conn_id, "ready", last_synced_at=datetime.now(UTC)
    )
    log.info("fivetran sync complete", extra={**entities, **metrics, "org_id": str(ctx.job.org_id)})


@handler("meta.download_creative")
async def download_creative(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await assets.download_creative(
            ctx,
            http,
            creative_id,
            p["asset_url"],
            p["kind"],
            verdict=_verdict(p),
            duration_ms=p.get("duration_ms"),
        )
    log.info("creative downloaded", extra={"creative_id": str(creative_id)})


async def _resolve_then_download(
    ctx: JobContext, source: str, lookup: Callable[[httpx.AsyncClient, dict], Awaitable[str]]
) -> None:
    """The paid half of getting a video that is not in the ad account's library. Each
    creative gets one Apify lookup ever, claimed in the video_resolution ledger before the
    call, so a retried or duplicated job never pays twice. Any failure is final and recorded.
    A found URL goes to meta.download_creative, the free half, which keeps its own retries."""
    p = ctx.job.payload
    org_id, creative_id = ctx.job.org_id, UUID(p["creative_id"])
    if not await repo.start_video_resolution(ctx.db, org_id, creative_id, source):
        log.info("video already looked up", extra={"creative_id": str(creative_id)})
        return
    try:
        async with httpx.AsyncClient(timeout=120) as http:
            video_url = await lookup(http, p)
    except Exception as exc:  # noqa: BLE001 -- every failure is final: Apify is paid per run
        await repo.finish_video_resolution(ctx.db, creative_id, video_url=None, error=str(exc))
        log.warning(
            "video lookup failed; not retried",
            extra={"creative_id": str(creative_id), "source": source, "error": str(exc)},
        )
        return
    await repo.finish_video_resolution(ctx.db, creative_id, video_url=video_url)
    await queue.enqueue(
        ctx.db,
        org_id,
        "meta.download_creative",
        {
            "creative_id": str(creative_id),
            "asset_url": video_url,
            **{k: p[k] for k in ("kind", "format", "format_source") if k in p},
        },
        dedup_key=str(creative_id),
    )
    log.info("resolved video via Apify", extra={"creative_id": str(creative_id), "source": source})


@handler("meta.resolve_instagram_video")
async def resolve_instagram_video(ctx: JobContext) -> None:
    async def lookup(http: httpx.AsyncClient, p: dict) -> str:
        return await instagram_scrape.resolve_video_url(http, ctx.settings, p["permalink_url"])

    await _resolve_then_download(ctx, "instagram", lookup)


@handler("meta.resolve_facebook_video")
async def resolve_facebook_video(ctx: JobContext) -> None:
    async def lookup(http: httpx.AsyncClient, p: dict) -> str:
        urls = await facebook_video.resolve_video_urls(http, ctx.settings, p["video_ids"])
        # The ids are in preference order; the first that plays wins.
        video_id = next((v for v in p["video_ids"] if v in urls), None)
        if video_id is None:
            raise facebook_video.FacebookVideoError(f"no public video among {p['video_ids']}")
        return urls[video_id]

    await _resolve_then_download(ctx, "facebook", lookup)


@handler("meta.download_carousel")
async def download_carousel(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await assets.download_carousel(ctx, http, creative_id, p["asset_urls"])
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
