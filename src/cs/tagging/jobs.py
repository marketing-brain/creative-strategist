from __future__ import annotations

import logging
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.insights import jobs as insight_jobs
from cs.jobs.registry import JobContext, handler
from cs.tagging import runner
from cs.tagging.tagger import GeminiGatewayTagger, Tagger

log = logging.getLogger("cs.tagging")


def default_tagger_factory(settings: Settings) -> Tagger:
    return GeminiGatewayTagger(settings)


TAGGER_FACTORY = default_tagger_factory


@handler("tagging.tag_creative")
async def tag_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    await runner.tag_creative(ctx, creative_id, TAGGER_FACTORY(ctx.settings))
    # The creative's one insight paragraph is written alongside its tags. It is a separate
    # job so a failed insight never re-runs tagging; it no-ops if one already exists or the
    # metrics are not readable yet (the nightly sweep picks those up later).
    await queue.enqueue(
        ctx.db,
        ctx.job.org_id,
        insight_jobs.CREATIVE_INSIGHT_JOB,
        {"creative_id": str(creative_id)},
        dedup_key=insight_jobs.creative_insight_dedup_key(creative_id),
        max_attempts=insight_jobs.CREATIVE_INSIGHT_MAX_ATTEMPTS,
    )
