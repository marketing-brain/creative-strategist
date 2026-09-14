"""Video length for every stored video file.

Retention is only compared between videos of similar length (diagnosis.LENGTH_BUCKETS_S),
so a video whose length is unknown drops out of every baseline. Downloads probe the length
as the file lands; this backfills files stored before that, or whose probe failed.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from cs.core import queue, repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule
from cs.tagging.video import probe_duration_ms

log = logging.getLogger("cs.ingest.durations")

PROBE_JOB = "assets.probe_video_duration"
PROBE_MAX_ATTEMPTS = 3
NIGHTLY_LIMIT = 500


def probe_dedup_key(creative_id: UUID) -> str:
    return f"probe_duration:{creative_id}"


@handler(PROBE_JOB)
async def probe_video_duration(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    org_id = ctx.job.org_id
    c = await repo.get_creative(ctx.db, org_id, creative_id)
    if c is None or c["primary_asset_mime"] != "video/mp4" or c["primary_asset_duration_ms"]:
        return
    data = await ctx.blobs.get(c["primary_asset_path"])
    duration_ms = await asyncio.to_thread(probe_duration_ms, data)
    await repo.set_asset_duration(ctx.db, org_id, c["primary_asset_id"], duration_ms)


async def unprobed_video_creatives(
    db: Database,
    limit: int,
    ad_account_id: UUID | None = None,
    exclude_creative_id: UUID | None = None,
) -> list:
    """Video creatives whose stored file has no length yet, skipping any whose probe job
    already gave up (a file ffprobe cannot read must not block anything forever)."""
    return await db.fetch(
        """
        SELECT c.id, c.org_id FROM creative c
        JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.format = 'video' AND a.mime = 'video/mp4' AND a.duration_ms IS NULL
          AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($4::uuid IS NULL OR c.id <> $4)
          AND NOT EXISTS (
            SELECT 1 FROM job j WHERE j.org_id = c.org_id AND j.job_type = $3
              AND j.dedup_key = 'probe_duration:' || c.id::text AND j.status = 'dead'
          )
        ORDER BY c.id
        LIMIT $1
        """,
        limit,
        ad_account_id,
        PROBE_JOB,
        exclude_creative_id,
    )


@register_schedule("assets.nightly_probe_video_durations", every_seconds=86400)
async def nightly_probe_video_durations(db: Database, settings: Settings) -> None:
    for r in await unprobed_video_creatives(db, NIGHTLY_LIMIT):
        await queue.enqueue(
            db,
            r["org_id"],
            PROBE_JOB,
            {"creative_id": str(r["id"])},
            dedup_key=probe_dedup_key(r["id"]),
            max_attempts=PROBE_MAX_ATTEMPTS,
        )
