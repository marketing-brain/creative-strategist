from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.creative_insight import GeminiGatewayInsightWriter, InsightWriter
from cs.insights.creative_insight_gates import FORMATS, NIGHTLY_SCHEDULE
from cs.insights.creative_insight_runner import write_creative_insight
from cs.insights.diagnosis import ATTENTION_FLOOR
from cs.insights.explain import _template
from cs.insights.fatigue import compute_fatigue
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

WINDOW_DAYS = 30


def default_insight_writer_factory(settings: Settings) -> InsightWriter:
    return GeminiGatewayInsightWriter(settings)


INSIGHT_WRITER_FACTORY = default_insight_writer_factory
CREATIVE_INSIGHT_JOB = "insights.creative_insight"
CREATIVE_INSIGHT_MAX_ATTEMPTS = 3


def creative_insight_dedup_key(creative_id: UUID) -> str:
    return f"creative_insight:{creative_id}"


async def _account(ctx: JobContext):
    return await ctx.db.fetchrow(
        "SELECT id, name FROM ad_account WHERE org_id=$1 AND id=$2",
        ctx.job.org_id,
        UUID(ctx.job.payload["ad_account_id"]),
    )


@handler("insights.compute_fatigue")
async def compute_fatigue_job(ctx: JobContext) -> None:
    acct = await _account(ctx)
    # The trailing window ends on the account's last synced day, not on the wall clock.
    last = await core_repo.account_last_metric_day(ctx.db, ctx.job.org_id, acct["id"])
    drafts = await compute_fatigue(ctx.db, ctx.job.org_id, acct["id"], last or date.today())
    # Fatigue is a current state, not a history: /next reads every fatigue row the account
    # has, so each run replaces the last one's. The text is the template -- nothing displays
    # it since the Fatigue tab went, so a model call per row would buy nothing.
    async with ctx.db.transaction() as tx:
        await tx.execute(
            "DELETE FROM insight WHERE org_id=$1 AND ad_account_id=$2 AND kind='fatigue'",
            ctx.job.org_id,
            acct["id"],
        )
        for d in drafts[:50]:
            await repo.insert_insight(tx, ctx.job.org_id, d, *_template(d))


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


@handler(CREATIVE_INSIGHT_JOB)
async def creative_insight(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    await write_creative_insight(ctx, creative_id, INSIGHT_WRITER_FACTORY(ctx.settings))


@register_schedule(NIGHTLY_SCHEDULE, every_seconds=86400)
async def nightly_creative_insights(db: Database, settings: Settings) -> None:
    """Pick up creatives that were too new to read when they were tagged, and videos, which
    are not tagged while video tagging is off. The candidate query skips any footage with a
    creative_insight row, and a failed paid call writes one, so nothing is paid for twice.
    A job that ran out of retries never reached the paid call and may be queued again."""
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
        room[slot] -= 1
        await queue.enqueue(
            db,
            r["org_id"],
            CREATIVE_INSIGHT_JOB,
            {"creative_id": str(r["id"])},
            dedup_key=creative_insight_dedup_key(r["id"]),
            max_attempts=CREATIVE_INSIGHT_MAX_ATTEMPTS,
        )
