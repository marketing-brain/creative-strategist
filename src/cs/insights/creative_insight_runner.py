from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from cs.core import repo as ingest_repo
from cs.ingest.durations import unprobed_video_creatives
from cs.insights import repo
from cs.insights.creative_insight import (
    SCHEMA,
    SYSTEM,
    InsightWriter,
    grounding_problems,
    is_readable,
    render_brief,
    user_text,
)
from cs.insights.diagnose_creative import diagnose_creative
from cs.jobs.registry import JobContext
from cs.tagging.prompts import prompt_sha
from cs.tagging.runner import load_media
from cs.tagging.video import probe_duration_ms

log = logging.getLogger("cs.insights.creative_insight")

FORMATS = ("image", "video", "carousel")

Outcome = Literal[
    "exists",
    "no_asset",
    "unresolved",
    "format_limit",
    "lengths_pending",
    "unreadable",
    "done",
    "rejected",
]


async def write_creative_insight(
    ctx: JobContext, creative_id: UUID, writer: InsightWriter, until: date | None = None
) -> Outcome:
    """Write the creative's one insight paragraph, if it has none yet and its metrics are
    readable. Unreadable creatives get nothing, so the nightly sweep can try again later.
    A draft that fails the grounding check is retried once with the problems named; a
    second failure is stored as 'rejected' and never shown."""
    org_id = ctx.job.org_id
    if await repo.get_creative_insight(ctx.db, org_id, creative_id) is not None:
        return "exists"
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    if creative is None or not creative["primary_asset_path"]:
        return "no_asset"
    fmt = creative["format"]
    if fmt not in FORMATS:
        return "unresolved"
    cap = ctx.settings.creative_insight_limit_per_format
    if await repo.count_creative_insights(ctx.db, org_id, fmt) >= cap:
        # Checked before any model call. One worker runs these jobs serially, so the count
        # cannot be overtaken between this check and the insert below.
        return "format_limit"
    if fmt == "video" and await unprobed_video_creatives(
        ctx.db, 1, creative["ad_account_id"], exclude_creative_id=creative_id
    ):
        # Retention compares only videos of similar length. Writing this once while peers'
        # lengths are still being backfilled would leave retention unreadable for good;
        # the nightly sweep tries again once they are probed.
        return "lengths_pending"
    until = until or datetime.now(UTC).date()
    diagnosed = await diagnose_creative(ctx.db, org_id, creative, until)
    if not is_readable(diagnosed):
        return "unreadable"

    images, video = await load_media(ctx, creative)
    duration_ms = creative["primary_asset_duration_ms"]
    if video is not None and duration_ms is None:
        # Timestamps in the brief need the runtime; assets stored before ingest probed it
        # are backfilled here.
        duration_ms = await asyncio.to_thread(probe_duration_ms, video[0])
        await ingest_repo.set_asset_duration(
            ctx.db, org_id, creative["primary_asset_id"], duration_ms
        )
        diagnosed = await diagnose_creative(ctx.db, org_id, creative, until, duration_ms)

    brief = render_brief(creative, diagnosed, duration_ms)
    feedback: list[str] | None = None
    input_tokens = output_tokens = 0
    for _ in range(2):
        text = user_text(brief, feedback)
        result = await writer.write(images, video, text)
        input_tokens += result.input_tokens
        output_tokens += result.output_tokens
        if result.refused:
            raise RuntimeError("model refused to write the creative insight")
        feedback = grounding_problems(result.paragraph, brief, fmt, duration_ms)
        if not feedback:
            break
        log.warning("creative insight %s failed grounding: %s", creative_id, feedback)
    status: Literal["done", "rejected"] = "rejected" if feedback else "done"
    dx = diagnosed.diagnosis
    await repo.insert_creative_insight(
        ctx.db,
        org_id,
        creative_id,
        status=status,
        paragraph=result.paragraph,
        moments=result.moments,
        problems=feedback or [],
        brief=brief,
        window_start=dx.window_start,
        window_end=dx.window_end,
        model=result.model or writer.model,
        prompt_sha256=prompt_sha(SYSTEM, user_text(brief), SCHEMA),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return status
