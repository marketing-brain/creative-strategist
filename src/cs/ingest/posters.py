"""Poster images: the small still a grid card shows.

Without one the browser has to open every mp4 on the page to paint a first frame, and pull
every image at full size (often over 1000px and several MB) to show it 200px wide. Both are
slow and cost storage egress on each view. A poster is a small JPEG made from our own stored
file, a frame for a video and a downscale for an image, so it covers every stored file,
never expires and is shared by every creative that runs the same file (the blob is keyed by
the file's sha256).

A creative whose media we could not retrieve has no file to cut from. For those the sync
copies Meta's own thumbnail (creative_history.thumbnail_url) into the blob store. Meta's
links expire within days, so they are downloaded, never hot-linked. They are small, often
under 200px, and are replaced by a real poster if the file ever arrives.

Display only. Both are assets of kind 'thumbnail': never a primary asset, never an input to
a format verdict or the tagger.

Backfill for creatives stored before this existed:  python -m cs.ingest.posters [org_id]
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import asyncpg
import httpx
from PIL import Image

from cs.connectors.fivetran.connect import schema_for
from cs.core import repo
from cs.core.blobs import BlobStore, sha256_hex
from cs.core.db import Database
from cs.tagging.images import prepare_image
from cs.tagging.video import _run

log = logging.getLogger("cs.ingest.posters")

POSTER_KIND = "thumbnail"
POSTER_WIDTH = 480
# One second in: the first frame of an ad is often black or a fade, and the frame a buyer
# recognises the video by is the hook. Files shorter than that fall back to the first frame.
POSTER_AT_S = (1.0, 0.0)


def extract_poster(video: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.mp4", Path(tmp) / "poster.jpg"
        src.write_bytes(video)
        for at in POSTER_AT_S:
            _run(
                ["ffmpeg", "-v", "error", "-y", "-ss", str(at), "-i", str(src), "-frames:v", "1"]
                + ["-vf", f"scale={POSTER_WIDTH}:-2", "-q:v", "4", str(out)]
            )
            if out.exists() and out.stat().st_size > 0:
                return out.read_bytes()
    raise RuntimeError("ffmpeg produced no frame")


def downscale(image: bytes) -> bytes:
    """To POSTER_WIDTH wide, whatever the height: cards are width-bound, and a portrait image
    sized by its long side would come out too narrow for a 2x screen."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = im.size
    if w > POSTER_WIDTH:
        im = im.resize((POSTER_WIDTH, round(h * POSTER_WIDTH / w)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82, optimize=True)
    return buf.getvalue()


def make_poster(data: bytes, mime: str) -> bytes:
    return extract_poster(data) if mime.startswith("video/") else downscale(data)


def poster_key(org_id: UUID, file_sha256: str) -> str:
    return f"{org_id}/posters/{file_sha256}.jpg"


async def ensure_poster(
    db: Database,
    blobs: BlobStore,
    org_id: UUID,
    creative_id: UUID,
    path: str,
    sha256: str,
    mime: str,
    data: bytes | None = None,
) -> bool:
    """Give the creative a poster row for its primary file (at `path`), making the image only
    if no creative running the same file has one yet. Returns whether a new image was made."""
    key = poster_key(org_id, sha256)
    have = await repo.list_assets_by_kind(db, org_id, creative_id, POSTER_KIND)
    if any(a["path"] == key for a in have):
        return False
    if have:
        # A Meta thumbnail kept while the file was missing. The file is here now.
        await repo.delete_assets_by_kind(db, org_id, creative_id, (POSTER_KIND,))
    twin = await db.fetchrow(
        "SELECT sha256, width, height FROM asset WHERE org_id=$1 AND kind=$2 AND path=$3 LIMIT 1",
        org_id,
        POSTER_KIND,
        key,
    )
    if twin is not None:
        await repo.insert_asset(
            db, org_id, creative_id, POSTER_KIND, key, twin["sha256"], "image/jpeg"
        )
        return False
    if data is None:
        data = await blobs.get(path)
    jpeg = await asyncio.to_thread(make_poster, data, mime)
    await blobs.put(key, jpeg, "image/jpeg")
    await repo.insert_asset(
        db, org_id, creative_id, POSTER_KIND, key, sha256_hex(jpeg), "image/jpeg"
    )
    return True


META_THUMBNAILS_PER_RUN = 200


async def meta_thumbnails(
    db: Database, blobs: BlobStore, org_id: UUID, limit: int = META_THUMBNAILS_PER_RUN
) -> int:
    """Copy Meta's thumbnail for creatives that have no stored file and no picture yet.
    Bounded per run so a first sync of a large account is not held up by pictures; the next
    sync carries on. Returns how many were stored."""
    try:
        rows = await db.fetch(
            f"""
            SELECT c.id, h.thumbnail_url FROM creative c
            JOIN ad_account aa ON aa.id = c.ad_account_id AND aa.status = 'active'
            JOIN LATERAL (
                SELECT thumbnail_url FROM "{schema_for(org_id)}".creative_history h
                WHERE h.id::text = c.platform_creative_id
                ORDER BY h._fivetran_synced DESC LIMIT 1
            ) h ON h.thumbnail_url IS NOT NULL
            WHERE c.org_id = $1 AND c.primary_asset_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM asset t WHERE t.creative_id = c.id AND t.kind = $2)
            LIMIT $3
            """,
            org_id,
            POSTER_KIND,
            limit,
        )
    except asyncpg.PostgresError as exc:
        log.warning("no Meta thumbnails for org %s: %s", org_id, exc)
        return 0
    stored = 0
    async with httpx.AsyncClient() as http:
        for r in rows:
            try:
                resp = await http.get(r["thumbnail_url"], follow_redirects=True, timeout=30)
                resp.raise_for_status()
                jpeg, w, h = await asyncio.to_thread(prepare_image, resp.content, POSTER_WIDTH)
                key = f"{org_id}/posters/meta/{r['id']}.jpg"
                await blobs.put(key, jpeg, "image/jpeg")
                await repo.insert_asset(
                    db, org_id, r["id"], POSTER_KIND, key, sha256_hex(jpeg), "image/jpeg", w, h
                )
                stored += 1
            except Exception as exc:
                # The exception text carries Meta's signed link; log only what kind it was.
                status = getattr(getattr(exc, "response", None), "status_code", "")
                log.warning(
                    "no Meta thumbnail for creative %s: %s %s", r["id"], type(exc).__name__, status
                )
    return stored


async def backfill(db: Database, blobs: BlobStore, org_id: UUID | None = None) -> None:
    """One file at a time, on purpose: this runs beside the live app and is not urgent."""
    rows = await db.fetch(
        """
        SELECT c.id, c.org_id, a.path, a.sha256, a.mime FROM creative c
        JOIN asset a ON a.id = c.primary_asset_id
        WHERE a.kind IN ('original_video', 'original_image')
          AND ($1::uuid IS NULL OR c.org_id = $1)
          AND NOT EXISTS (
            SELECT 1 FROM asset t WHERE t.creative_id = c.id AND t.kind = $2
              AND t.path NOT LIKE '%/posters/meta/%')
        ORDER BY a.sha256
        """,
        org_id,
        POSTER_KIND,
    )
    cut = failed = 0
    for i, r in enumerate(rows, 1):
        try:
            cut += await ensure_poster(
                db, blobs, r["org_id"], r["id"], r["path"], r["sha256"], r["mime"]
            )
        except Exception as exc:
            failed += 1
            log.warning("no poster for creative %s: %s", r["id"], exc)
        if i % 25 == 0:
            log.info("%d/%d creatives, %d images cut, %d failed", i, len(rows), cut, failed)
    log.info("done: %d creatives, %d images cut, %d failed", len(rows), cut, failed)


async def _main() -> None:
    from cs.core.blobs import blob_store_from_settings
    from cs.core.config import Settings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    settings = Settings()
    db = await Database.connect(settings.database_url)
    try:
        org = UUID(sys.argv[1]) if len(sys.argv) > 1 else None
        blobs = blob_store_from_settings(settings)
        await backfill(db, blobs, org)
        orgs = [org] if org else [r["id"] for r in await db.fetch("SELECT id FROM org")]
        for o in orgs:
            n = await meta_thumbnails(db, blobs, o, limit=5000)
            log.info("org %s: %d Meta thumbnails stored", o, n)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(_main())
