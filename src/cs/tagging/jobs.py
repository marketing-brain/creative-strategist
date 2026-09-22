from __future__ import annotations

import logging
from uuid import UUID

from cs.core.config import Settings
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
