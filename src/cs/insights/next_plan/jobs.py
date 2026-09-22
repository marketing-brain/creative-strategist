"""Compute and store one `/next` plan per ad account.

Costs no paid call: the plan is assembled from the stored account-read run and the 30-day
rollups, and the only model-written text it carries is `next_tests`, already paid for by the
read. So, unlike every writer job, this schedule is not one of the paused ones.

It runs after the nightly account read rather than beside it, because a plan built from
yesterday's run would contradict today's `/insights`.
"""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, date, datetime
from uuid import UUID

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights.account_read import repo as read_repo
from cs.insights.diagnose_creative import diagnose_creative
from cs.insights.next_plan import repo
from cs.insights.next_plan.allocate import DiagnosedAd, choose_decline
from cs.insights.next_plan.allocate import build as build_allocation
from cs.insights.next_plan.build import build_plan
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.next_plan")
JOB = "insights.next_plan"

SPEND_FLOOR = 250.0  # below this a row is not a budget decision worth a page slot
MAX_DIAGNOSED = 40  # each costs a diagnose_creative call; the job is bounded, not exhaustive


async def _diagnose_all(
    db: Database, org_id: UUID, ad_account_id: UUID
) -> tuple[list[DiagnosedAd], float, date | None]:
    account_spend = await repo.account_spend_30d(db, org_id, ad_account_id)
    rows = await repo.allocation_candidates(
        db, org_id, ad_account_id, spend_floor=SPEND_FLOOR, limit=MAX_DIAGNOSED
    )
    last = await core_repo.account_last_metric_day(db, org_id, ad_account_id)
    if not rows:
        return [], account_spend, last
    fatigue = await repo.fatigue_rows(db, org_id, ad_account_id)
    until = last or datetime.now(UTC).date()
    out: list[DiagnosedAd] = []
    for r in rows:
        creative = await core_repo.get_creative(db, org_id, r["id"])
        if not creative:
            continue
        members = await core_repo.footage_member_ids(db, org_id, r["id"])
        try:
            d = await diagnose_creative(db, org_id, creative, until, member_ids=members)
        except Exception:  # noqa: BLE001 - one unreadable ad must not lose the whole plan
            log.exception("diagnosis failed", extra={"creative_id": str(r["id"])})
            continue
        v = d.diagnosis.verdict
        broken = next((t for t in d.diagnosis.tiers if t.tier == d.diagnosis.broken_tier), None)
        out.append(
            DiagnosedAd(
                creative_id=str(r["id"]),
                headline=(r["title"] or "(no copy)").strip(),
                rep_creative_id=str(r["id"]),
                spend_30d=float(r["spend"] or 0),
                verdict_kind=v.kind,
                verdict_headline=v.headline,
                verdict_detail=v.detail,
                broken_layer=broken.name if broken else None,
                # Every copy of the footage, so an ad wearing out under one creative id is not
                # missed because the group is represented by another.
                decline=choose_decline([x for m in members for x in fatigue.get(m, [])]),
            )
        )
    return out, account_spend, last


@handler(JOB)
async def next_plan(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    ad_account_id = UUID(ctx.job.payload["ad_account_id"])
    run = await read_repo.latest_run(ctx.db, org_id, ad_account_id)
    if run is None or run["status"] != "done":
        await repo.insert_run(
            ctx.db,
            org_id,
            ad_account_id,
            status="insufficient",
            plan={"reason": "no finished account read for this account yet"},
            account_read_run_id=run["id"] if run else None,
        )
        return
    try:
        result = await read_repo.run_result(ctx.db, run["id"])
        plan = build_plan(result, run.get("read"))
        ads, account_spend, data_through = await _diagnose_all(ctx.db, org_id, ad_account_id)
        plan.allocate, plan.allocate_empty_reason = build_allocation(ads, account_spend)
        plan.data_through = data_through.isoformat() if data_through else None
    except Exception as exc:  # noqa: BLE001 - a failed plan must not hide the previous one
        await repo.insert_run(
            ctx.db,
            org_id,
            ad_account_id,
            status="failed",
            plan={"reason": "plan failed"},
            account_read_run_id=run["id"],
            error=f"{exc}\n{traceback.format_exc()}",
        )
        raise
    payload = plan.to_dict()
    await repo.insert_run(
        ctx.db,
        org_id,
        ad_account_id,
        status="done",
        plan=payload,
        account_read_run_id=run["id"],
    )
    await repo.upsert_items(ctx.db, org_id, ad_account_id, _items(payload))
    log.info(
        "next plan stored",
        extra={
            "ad_account_id": str(ad_account_id),
            "allocate": len(plan.allocate),
            "make": len(plan.make),
            "explore": len(plan.explore),
        },
    )


def _items(payload: dict) -> list[tuple[str, str, dict]]:
    """Every recommendation this run emitted, for the ledger. Explore rows are included: an
    account that finally shoots a never-tried angle should close that row, not just drop it."""
    out: list[tuple[str, str, dict]] = []
    for row in payload.get("allocate") or []:
        out.append((row["key"], row["kind"], {"creative_id": row["creative_id"]}))
    for b in payload.get("make") or []:
        out.append((b["key"], "make", {"attributes": b["attributes"], "title": b["title"]}))
    for e in payload.get("explore") or []:
        out.append((e["key"], "explore", {"dimension": e["dimension"], "value": e["value"]}))
    return out


@register_schedule("insights.nightly_next_plan", every_seconds=86400)
async def nightly_next_plan(db: Database, settings: Settings) -> None:
    """A daily rebuild on top of the one `compute_account_read` enqueues when it finishes.

    Not a duplicate: the allocation block reads the 30-day rollups, which move whenever a sync
    lands, so an ad crosses the fatigue line or falls out of the window on days when no account
    read runs at all. The read's own enqueue covers the other direction -- new patterns should
    reach the plan the moment they exist, not up to a day later.

    The two collapse when they collide: `dedup_key` blocks a second pending job of the same
    identity, and the handler reads whatever the latest *done* read is, so a plan built while
    a read is mid-flight builds on the previous one rather than half of the new one.
    """
    for a in await db.fetch("SELECT id, org_id FROM ad_account WHERE status='active'"):
        await queue.enqueue(
            db,
            a["org_id"],
            JOB,
            {"ad_account_id": str(a["id"])},
            dedup_key=f"next_plan:{a['id']}",
            max_attempts=2,
        )
    n = await repo.purge_runs(db, 30)
    if n:
        log.info("purged next plan runs", extra={"count": n})
