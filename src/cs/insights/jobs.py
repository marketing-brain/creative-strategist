from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.brief import render_brief
from cs.insights.creative_insight import GeminiGatewayInsightWriter, InsightWriter
from cs.insights.creative_insight_runner import FORMATS, write_creative_insight
from cs.insights.diagnosis import ATTENTION_FLOOR
from cs.insights.explain import Explainer, GeminiGatewayExplainer
from cs.insights.fatigue import compute_fatigue
from cs.insights.types import InsightDraft
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule
from cs.tagging.taxonomy import load_taxonomy

WINDOW_DAYS = 30


def default_explainer_factory(settings: Settings) -> Explainer:
    return GeminiGatewayExplainer(settings)


EXPLAINER_FACTORY = default_explainer_factory


def default_insight_writer_factory(settings: Settings) -> InsightWriter:
    return GeminiGatewayInsightWriter(settings)


INSIGHT_WRITER_FACTORY = default_insight_writer_factory
CREATIVE_INSIGHT_JOB = "insights.creative_insight"
CREATIVE_INSIGHT_MAX_ATTEMPTS = 3


def creative_insight_dedup_key(creative_id: UUID) -> str:
    return f"creative_insight:{creative_id}"


async def _persist(ctx: JobContext, drafts: list[InsightDraft], account_name: str) -> int:
    explainer = EXPLAINER_FACTORY(ctx.settings)
    dims = {d.name: d.description for d in load_taxonomy().dimensions}
    n = 0
    for d in drafts:
        exists = await ctx.db.fetchval(
            "SELECT 1 FROM insight WHERE org_id=$1 AND kind=$2 AND dimension=$3 AND value=$4 "
            "AND metric=$5 AND window_start=$6 AND window_end=$7 "
            "AND creative_id IS NOT DISTINCT FROM $8",
            ctx.job.org_id,
            d.kind,
            d.dimension,
            d.value,
            d.metric,
            d.window_start,
            d.window_end,
            d.creative_id,
        )
        if exists:
            continue
        context = {"dimension_description": dims.get(d.dimension), "account_name": account_name}
        if d.creative_id:
            context["creative_headline"] = await ctx.db.fetchval(
                "SELECT headline FROM creative WHERE id=$1", d.creative_id
            )
        explanation, test = await explainer.explain(d, context)
        if await repo.insert_insight(ctx.db, ctx.job.org_id, d, explanation, test):
            n += 1
    return n


async def _account(ctx: JobContext):
    return await ctx.db.fetchrow(
        "SELECT id, name FROM ad_account WHERE org_id=$1 AND id=$2",
        ctx.job.org_id,
        UUID(ctx.job.payload["ad_account_id"]),
    )


@handler("insights.compute_fatigue")
async def compute_fatigue_job(ctx: JobContext) -> None:
    acct = await _account(ctx)
    drafts = await compute_fatigue(ctx.db, ctx.job.org_id, acct["id"], date.today())
    await _persist(ctx, drafts[:50], acct["name"])


async def _enqueue_for_active(db: Database, job_type: str, prefix: str) -> None:
    for a in await db.fetch("SELECT id, org_id FROM ad_account WHERE status='active'"):
        await queue.enqueue(
            db,
            a["org_id"],
            job_type,
            {"ad_account_id": str(a["id"])},
            dedup_key=f"{prefix}:{a['id']}",
        )


@register_schedule("insights.periodic_fatigue", every_seconds=21600)
async def periodic_fatigue(db: Database, settings: Settings) -> None:
    await _enqueue_for_active(db, "insights.compute_fatigue", "fatigue")


@handler("insights.weekly_brief")
async def weekly_brief(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    rows = await ctx.db.fetch(
        "SELECT i.*, c.headline AS creative_headline FROM insight i "
        "LEFT JOIN creative c ON c.id = i.creative_id "
        "WHERE i.org_id=$1 AND i.created_at >= now() - interval '7 days' ORDER BY abs(i.lift) DESC",
        org_id,
    )
    fatigue = [dict(r) for r in rows if r["kind"] == "fatigue"][:10]
    await repo.insert_brief(
        ctx.db,
        org_id,
        week_start,
        render_brief(week_start, fatigue),
        [r["id"] for r in rows if r["kind"] == "fatigue"],
    )


@register_schedule("insights.weekly_brief", every_seconds=86400)
async def weekly_brief_schedule(db: Database, settings: Settings) -> None:
    today = date.today()
    if today.weekday() != 0:
        return
    week_start = today.isoformat()
    for o in await db.fetch("SELECT id FROM org"):
        await queue.enqueue(
            db, o["id"], "insights.weekly_brief", {}, dedup_key=f"brief:{o['id']}:{week_start}"
        )


@handler(CREATIVE_INSIGHT_JOB)
async def creative_insight(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    await write_creative_insight(ctx, creative_id, INSIGHT_WRITER_FACTORY(ctx.settings))


@register_schedule("insights.nightly_creative_insights", every_seconds=86400)
async def nightly_creative_insights(db: Database, settings: Settings) -> None:
    """Pick up creatives that were too new to read when they were tagged, and videos, which
    are not tagged while video tagging is off. Each still gets one insight at most;
    creatives whose job already exhausted its retries are left alone."""
    until = datetime.now(UTC).date()
    rows = await repo.creative_insight_candidates(
        db,
        until - timedelta(days=WINDOW_DAYS - 1),
        until,
        FORMATS,
        ATTENTION_FLOOR,
        settings.creative_insight_nightly_limit,
        settings.creative_insight_limit_per_format,
    )
    # Queue no more per org and format than the cap still allows; the job re-checks.
    room: dict[tuple[UUID, str], int] = {}
    for r in rows:
        slot = (r["org_id"], r["format"])
        if slot not in room:
            written = await repo.count_creative_insights(db, r["org_id"], r["format"])
            room[slot] = settings.creative_insight_limit_per_format - written
        if room[slot] <= 0:
            continue
        key = creative_insight_dedup_key(r["id"])
        if await queue.is_dead(db, r["org_id"], CREATIVE_INSIGHT_JOB, key):
            continue
        room[slot] -= 1
        await queue.enqueue(
            db,
            r["org_id"],
            CREATIVE_INSIGHT_JOB,
            {"creative_id": str(r["id"])},
            dedup_key=key,
            max_attempts=CREATIVE_INSIGHT_MAX_ATTEMPTS,
        )
