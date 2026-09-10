from __future__ import annotations

from datetime import date, timedelta
from uuid import UUID

from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.brief import render_brief
from cs.insights.explain import Explainer, GeminiGatewayExplainer
from cs.insights.fatigue import compute_fatigue
from cs.insights.patterns import compute_pattern_insights
from cs.insights.types import InsightDraft
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule
from cs.tagging.taxonomy import load_taxonomy

WINDOW_DAYS = 30


def default_explainer_factory(settings: Settings) -> Explainer:
    return GeminiGatewayExplainer(settings)


EXPLAINER_FACTORY = default_explainer_factory


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


@handler("insights.compute_patterns")
async def compute_patterns(ctx: JobContext) -> None:
    acct = await _account(ctx)
    end = date.today() - timedelta(days=1)
    drafts = await compute_pattern_insights(
        ctx.db, ctx.job.org_id, acct["id"], end - timedelta(days=WINDOW_DAYS - 1), end
    )
    await _persist(ctx, drafts, acct["name"])


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


@register_schedule("insights.nightly_patterns", every_seconds=86400)
async def nightly_patterns(db: Database, settings: Settings) -> None:
    await _enqueue_for_active(db, "insights.compute_patterns", "patterns")


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
    patterns = [dict(r) for r in rows if r["kind"] == "pattern"][:10]
    fatigue = [dict(r) for r in rows if r["kind"] == "fatigue"][:10]
    await repo.insert_brief(
        ctx.db,
        org_id,
        week_start,
        render_brief(week_start, patterns, fatigue),
        [r["id"] for r in rows],
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
