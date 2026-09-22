from __future__ import annotations

from datetime import date
from uuid import UUID

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.explain import _template
from cs.insights.fatigue import compute_fatigue
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule


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
