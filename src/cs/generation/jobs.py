from __future__ import annotations

from uuid import UUID

from cs.core.config import Settings
from cs.generation import service
from cs.generation.copy import ClaudeCopyWriter, CopyWriter
from cs.generation.images import GatewayImageGenerator, ImageGenerator
from cs.generation.video import NodeVideoGenerator, VideoGenerator
from cs.jobs.registry import JobContext, handler


def default_copywriter(settings: Settings) -> CopyWriter:
    return ClaudeCopyWriter(settings)


def default_image_gen(settings: Settings) -> ImageGenerator:
    return GatewayImageGenerator(settings)


def default_video_gen(settings: Settings) -> VideoGenerator:
    return NodeVideoGenerator(settings)


COPYWRITER_FACTORY = default_copywriter
IMAGE_FACTORY = default_image_gen
VIDEO_FACTORY = default_video_gen


@handler("generation.run")
async def run(ctx: JobContext) -> None:
    await service.run_generation(
        ctx,
        UUID(ctx.job.payload["generation_id"]),
        COPYWRITER_FACTORY(ctx.settings),
        IMAGE_FACTORY(ctx.settings),
        VIDEO_FACTORY(ctx.settings),
    )
