from __future__ import annotations

from uuid import UUID

import httpx

from cs.core.config import Settings
from cs.jobs.registry import JobContext, handler
from cs.tagging import runner
from cs.tagging.tagger import ClaudeTagger, Tagger
from cs.tagging.video import GatewayTranscriber, Transcriber


def default_tagger_factory(settings: Settings) -> Tagger:
    return ClaudeTagger(settings)


def default_transcriber_factory(settings: Settings, http: httpx.AsyncClient) -> Transcriber:
    return GatewayTranscriber(settings, http)


TAGGER_FACTORY = default_tagger_factory
TRANSCRIBER_FACTORY = default_transcriber_factory


@handler("tagging.tag_creative")
async def tag_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    async with httpx.AsyncClient(timeout=300) as http:
        await runner.tag_creative(
            ctx, creative_id, TAGGER_FACTORY(ctx.settings), TRANSCRIBER_FACTORY(ctx.settings, http)
        )
