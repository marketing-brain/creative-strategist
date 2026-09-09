from __future__ import annotations

from uuid import UUID

from cs.core.config import Settings
from cs.generation import repo, service
from cs.generation.copy import CopyWriter, GeminiGatewayCopyWriter
from cs.generation.images import GatewayImageGenerator, ImageGenerator
from cs.generation.video import NodeVideoGenerator, VideoGenerator
from cs.jobs.registry import JobContext, handler


def default_copywriter(settings: Settings) -> CopyWriter:
    return GeminiGatewayCopyWriter(settings)


def default_image_gen(settings: Settings) -> ImageGenerator:
    return GatewayImageGenerator(settings)


def default_video_gen(settings: Settings) -> VideoGenerator:
    return NodeVideoGenerator(settings)


COPYWRITER_FACTORY = default_copywriter
IMAGE_FACTORY = default_image_gen
VIDEO_FACTORY = default_video_gen


@handler("generation.run")
async def run(ctx: JobContext) -> None:
    generation_id = UUID(ctx.job.payload["generation_id"])
    # Building these can fail (e.g. missing provider credentials). That must land on the
    # generation row the same way a failure inside run_generation does -- otherwise the row
    # is left at its prior status (usually 'pending') forever, with the job dying silently
    # after its retries and no error ever surfacing to the user.
    try:
        copywriter = COPYWRITER_FACTORY(ctx.settings)
        image_gen = IMAGE_FACTORY(ctx.settings)
        video_gen = VIDEO_FACTORY(ctx.settings)
    except Exception as exc:
        await repo.set_generation_status(
            ctx.db, ctx.job.org_id, generation_id, "failed", error=str(exc)[:2000]
        )
        raise
    await service.run_generation(ctx, generation_id, copywriter, image_gen, video_gen)
