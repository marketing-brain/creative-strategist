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


from cs.ingest import repo as ingest_repo  # noqa: E402
from cs.tagging import repo as tag_repo  # noqa: E402
from cs.tagging.embed import Embedder, GatewayEmbedder, tag_text  # noqa: E402


def default_embedder_factory(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    return GatewayEmbedder(settings, http)


EMBEDDER_FACTORY = default_embedder_factory


@handler("tagging.embed_creative")
async def embed_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    org_id = ctx.job.org_id
    run_id = await tag_repo.latest_run_id(ctx.db, org_id, creative_id)
    if run_id is None:
        raise RuntimeError("no completed tag run to embed")
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    tags = await tag_repo.latest_tags(ctx.db, org_id, creative_id)
    text = tag_text(
        {
            "headline": creative["headline"],
            "primary_text": creative["primary_text"],
        },
        tags,
    )
    async with httpx.AsyncClient() as http:
        [vec] = await EMBEDDER_FACTORY(ctx.settings, http).embed([text])
    await tag_repo.upsert_embedding(ctx.db, org_id, creative_id, run_id, vec)
