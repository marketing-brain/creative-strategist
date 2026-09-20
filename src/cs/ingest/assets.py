from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import httpx

from cs.core import queue, repo
from cs.core.blobs import sha256_hex
from cs.ingest.posters import ensure_poster
from cs.jobs.registry import JobContext
from cs.tagging.video import probe_duration_ms

log = logging.getLogger("cs.ingest.assets")

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    resp = await http.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]


async def _poster(ctx: JobContext, creative_id: UUID, key: str, data: bytes, mime: str) -> None:
    """Display only, so a file that cannot be read still ingests; the grid falls back to the
    file itself."""
    try:
        await ensure_poster(
            ctx.db, ctx.blobs, ctx.job.org_id, creative_id, key, sha256_hex(data), mime, data
        )
    except Exception as exc:
        log.warning("no poster for creative %s: %s", creative_id, exc)


async def download_creative(
    ctx: JobContext,
    http: httpx.AsyncClient,
    creative_id: UUID,
    asset_url: str,
    kind: str,
    *,
    verdict: tuple[str, str] | None = None,
    duration_ms: int | None = None,
) -> None:
    """verdict: the (format, format_source) the creative was kept unresolved for until its
    file existed; stored once the file is."""
    org_id = ctx.job.org_id
    data, mime = await _fetch(http, asset_url)
    ext = _EXT_BY_MIME.get(mime, "bin")
    key = f"{org_id}/creatives/{creative_id}/original.{ext}"
    await ctx.blobs.put(key, data, mime)
    if kind == "original_video" and duration_ms is None:
        # Meta's ad_video_history.length covers the account's own library and is passed in
        # by the sync; ffprobe is the fallback for videos resolved from elsewhere (Apify).
        # Unprobeable files still store; the insight job retries the probe.
        try:
            duration_ms = await asyncio.to_thread(probe_duration_ms, data)
        except Exception as exc:
            log.warning("could not probe duration for creative %s: %s", creative_id, exc)
    asset_id = await repo.insert_asset(
        ctx.db, org_id, creative_id, kind, key, sha256_hex(data), mime, duration_ms=duration_ms
    )
    await repo.set_primary_asset(ctx.db, org_id, creative_id, asset_id)
    await _poster(ctx, creative_id, key, data, mime)
    if verdict is not None:
        await repo.set_creative_format(ctx.db, org_id, creative_id, *verdict)
    if kind != "original_video" or ctx.settings.video_tagging_enabled:
        await queue.enqueue(
            ctx.db,
            org_id,
            "tagging.tag_creative",
            {"creative_id": str(creative_id)},
            dedup_key=f"tag:{creative_id}",
        )


async def download_carousel(
    ctx: JobContext, http: httpx.AsyncClient, creative_id: UUID, asset_urls: list[str]
) -> None:
    org_id = ctx.job.org_id
    primary_id = None
    for i, url in enumerate(asset_urls):
        existing = await repo.get_asset_by_seq(ctx.db, org_id, creative_id, "original_image", i)
        if existing is not None:
            asset_id = existing["id"]
        else:
            data, mime = await _fetch(http, url)
            ext = {"image/png": "png", "image/jpeg": "jpg"}.get(mime, "jpg")
            key = f"{org_id}/creatives/{creative_id}/card_{i:02d}.{ext}"
            await ctx.blobs.put(key, data, mime)
            asset_id = await repo.insert_asset(
                ctx.db, org_id, creative_id, "original_image", key, sha256_hex(data), mime, seq=i
            )
            if i == 0:
                await _poster(ctx, creative_id, key, data, mime)
        if i == 0:
            primary_id = asset_id
    if primary_id is not None:
        await repo.set_primary_asset(ctx.db, org_id, creative_id, primary_id)
        await queue.enqueue(
            ctx.db,
            org_id,
            "tagging.tag_creative",
            {"creative_id": str(creative_id)},
            dedup_key=f"tag:{creative_id}",
        )
