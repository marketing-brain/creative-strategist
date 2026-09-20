from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from cs.core import repo as ingest_repo
from cs.insights import repo
from cs.insights.creative_insight import (
    SYSTEM,
    InsightWriter,
    grounding_problems,
    render_brief,
    user_text,
)
from cs.insights.creative_insight_gates import insight_blocker
from cs.insights.diagnose_creative import diagnose_creative
from cs.jobs.registry import JobContext
from cs.tagging.prompts import prompt_sha
from cs.tagging.runner import load_media
from cs.tagging.video import probe_duration_ms

log = logging.getLogger("cs.insights.creative_insight")

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
    """Write the creative's one insight paragraph, if its footage has none yet and its
    metrics are readable. The paragraph is about the file, so it is written once per footage
    group over the group's pooled numbers, and every copy of the file shows it. Unreadable
    creatives get nothing, so the nightly sweep can try again later.
    A draft that fails the grounding check is retried once with the problems named; a
    second failure is stored as 'rejected' and never shown."""
    org_id = ctx.job.org_id
    member_ids = await ingest_repo.footage_member_ids(ctx.db, org_id, creative_id)
    if await repo.get_footage_insight(ctx.db, org_id, member_ids) is not None:
        return "exists"
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    if creative is None:
        return "no_asset"
    fmt = creative["format"]
    # Same anchor the creative page uses, so the paragraph and the panel read one window.
    if until is None:
        until = (
            await ingest_repo.account_last_metric_day(ctx.db, org_id, creative["ad_account_id"])
            or datetime.now(UTC).date()
        )
    # One worker runs these jobs serially, so the cap count checked here cannot be
    # overtaken before the insert below.
    blocker, diagnosed = await insight_blocker(
        ctx.db, ctx.settings, org_id, creative, until, member_ids
    )
    if blocker is not None:
        return blocker
    assert diagnosed is not None

    images, video = await load_media(ctx, creative)
    duration_ms = creative["primary_asset_duration_ms"]
    if video is not None and duration_ms is None:
        # Timestamps in the brief need the runtime; assets stored before ingest probed it
        # are backfilled here.
        duration_ms = await asyncio.to_thread(probe_duration_ms, video[0])
        await ingest_repo.set_asset_duration(
            ctx.db, org_id, creative["primary_asset_id"], duration_ms
        )
        diagnosed = await diagnose_creative(
            ctx.db, org_id, creative, until, duration_ms, member_ids
        )

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
        creative_profile=result.creative_profile,
        moments=result.moments,
        problems=feedback or [],
        brief=brief,
        window_start=dx.window_start,
        window_end=dx.window_end,
        model=result.model or writer.model,
        prompt_sha256=prompt_sha(SYSTEM, user_text(brief), {}),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    return status
