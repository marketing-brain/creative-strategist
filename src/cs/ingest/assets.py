from __future__ import annotations

from uuid import UUID

import httpx

from cs.core import queue, repo
from cs.core.blobs import sha256_hex
from cs.jobs.registry import JobContext

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    resp = await http.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]


async def download_creative(
    ctx: JobContext, http: httpx.AsyncClient, creative_id: UUID, asset_url: str, kind: str
) -> None:
    org_id = ctx.job.org_id
    data, mime = await _fetch(http, asset_url)
    ext = _EXT_BY_MIME.get(mime, "bin")
    key = f"{org_id}/creatives/{creative_id}/original.{ext}"
    await ctx.blobs.put(key, data, mime)
    asset_id = await repo.insert_asset(
        ctx.db, org_id, creative_id, kind, key, sha256_hex(data), mime
    )
    await repo.set_primary_asset(ctx.db, org_id, creative_id, asset_id)
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
