from __future__ import annotations

import logging
from uuid import UUID

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule
from cs.tagging import runner
from cs.tagging.gates import TAG_JOB, explain, tag_blocker
from cs.tagging.tagger import GeminiGatewayTagger, Tagger

log = logging.getLogger("cs.tagging")
NIGHTLY_SCHEDULE = "tagging.nightly_sweep"


def default_tagger_factory(settings: Settings) -> Tagger:
    return GeminiGatewayTagger(settings)


TAGGER_FACTORY = default_tagger_factory


@handler(TAG_JOB)
async def tag_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    creative = await core_repo.get_creative(ctx.db, ctx.job.org_id, creative_id)
    if creative is None:
        raise RuntimeError(f"creative {creative_id} is gone")
    # Re-checked here because this is where the money is spent. A job can sit in the queue
    # while a second copy of the same file is tagged, or while the switch goes off; the
    # enqueue-time check cannot see either. `force` in the payload is a human saying yes
    # anyway (the retag endpoint, scripts/enqueue_tagging.py --force).
    if not ctx.job.payload.get("force"):
        blocker, facts = await tag_blocker(ctx.db, ctx.settings, ctx.job.org_id, creative)
        if blocker is not None:
            log.info(
                "skipping tag run for creative %s: %s",
                creative_id,
                explain(blocker, facts),
                extra={"creative_id": str(creative_id), "blocker": blocker},
            )
            return
    await runner.tag_creative(ctx, creative_id, TAGGER_FACTORY(ctx.settings))


# One creative per file: the copies pool their delivery, and whichever copy is tagged, the
# file's tags are read from the highest-spend one that has them.
_CANDIDATES = """
WITH grp AS (
    SELECT c.id, c.org_id, c.format, c.ad_account_id, a.sha256,
           coalesce(sum(m.impressions), 0) AS impressions,
           coalesce(sum(m.video_plays), 0) AS plays,
           coalesce(sum(m.spend), 0) AS spend
    FROM creative c
    JOIN asset a ON a.id = c.primary_asset_id AND a.sha256 IS NOT NULL
    JOIN ad_account aa ON aa.id = c.ad_account_id AND aa.status = 'active'
    LEFT JOIN metric_daily m ON m.creative_id = c.id
    WHERE c.format = ANY($1::text[])
    GROUP BY c.id, c.org_id, c.format, c.ad_account_id, a.sha256
),
files AS (
    SELECT ad_account_id, format, sha256,
           sum(impressions) AS impressions, sum(plays) AS plays, sum(spend) AS spend,
           (array_agg(id ORDER BY spend DESC, id))[1] AS rep_id,
           (array_agg(org_id))[1] AS org_id
    FROM grp GROUP BY ad_account_id, format, sha256
)
SELECT f.rep_id AS id, f.org_id, f.format, f.impressions, f.plays, f.spend
FROM files f
WHERE (f.format <> 'video' OR $2::boolean)
  AND (f.impressions >= $3 OR (f.format = 'video' AND f.plays >= $4))
  AND (
    SELECT count(*) FROM tag_run r
    JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
    JOIN creative peer ON peer.id = r.creative_id
    JOIN asset pa ON pa.id = peer.primary_asset_id AND pa.sha256 = f.sha256
    WHERE peer.ad_account_id = f.ad_account_id AND peer.format = f.format
      AND r.status = 'done' AND coalesce(r.input_tokens, 0) > 0
      AND split_part(v.version, '.', 1)::int >= CASE WHEN f.format = 'video' THEN 2 ELSE 1 END
  ) < $5
ORDER BY f.spend DESC, f.rep_id
LIMIT $6
"""


async def tagging_candidates(db: Database, settings: Settings) -> list:
    return await db.fetch(
        _CANDIDATES,
        ["image", "video", "carousel"],
        settings.video_tagging_enabled,
        settings.tagging_min_impressions,
        settings.tagging_min_video_plays,
        settings.tagging_repeat_per_footage,
        settings.tagging_nightly_limit,
    )


@register_schedule(NIGHTLY_SCHEDULE, every_seconds=86400)
async def nightly_tagging_sweep(db: Database, settings: Settings) -> None:
    """Files that have since earned a tag. A creative arrives with no delivery at all, so
    the gate turns it away at download; this is what picks it up once it has run, highest
    spend first and capped per night. The job re-checks the gate, so a queued candidate that
    stops qualifying costs nothing."""
    rows = await tagging_candidates(db, settings)
    queued = 0
    for r in rows:
        if await queue.enqueue(
            db, r["org_id"], TAG_JOB, {"creative_id": str(r["id"])}, dedup_key=f"tag:{r['id']}"
        ):
            queued += 1
    if queued:
        log.info("tagging sweep queued %d of %d candidates", queued, len(rows), extra={"n": queued})
