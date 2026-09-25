"""Compute and store one `/next` plan per ad account.

The statistics cost nothing: allocation rows come from the 30-day rollups and a diagnosis per
ad, recut cards from the footage tables, and `next_tests` was already paid for by the account
read. Every card is complete and publishable with template wording at that point.

The prose does cost, so it is gated on `NARRATION_SCHEDULE` being unpaused, the same switch
that holds the writer jobs. A paused account still gets its full plan; it gets the wording the
templates produce instead of the wording the model produces, which is a difference in polish
rather than in content. Narration is cached on the rounded measurements, so an account whose
numbers have not moved pays nothing on the second night either.

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
from cs.insights.next_plan import card as card_mod
from cs.insights.next_plan import clip, finding, ledger, ledger_repo, narrate, repo, segment_repo
from cs.insights.next_plan.allocate import DiagnosedAd, choose_decline
from cs.insights.next_plan.allocate import build as build_allocation
from cs.insights.next_plan.build import build_plan
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule, schedule_paused

log = logging.getLogger("cs.next_plan")
JOB = "insights.next_plan"

SPEND_FLOOR = 250.0  # below this a row is not a budget decision worth a page slot
MAX_DIAGNOSED = 40  # each costs a diagnose_creative call; the job is bounded, not exhaustive
MAX_CARDS = 3  # a week's work, not a backlog (docs/next-surface.md §1.5)
NARRATION_SCHEDULE = "insights.next_plan_narration"  # pause this to stop paying for prose


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
                footage_id=str(r["footage_id"]) if r["footage_id"] else None,
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


async def _cards(
    ctx: JobContext, org_id: UUID, ad_account_id: UUID, result: dict, read: dict | None
) -> tuple[list[dict], str | None]:
    """The card block: the account read's own findings, aimed at the files that spend."""
    from cs.insights.account_read.types import unit_from_json

    candidates, blocked = finding.candidates(result, read)
    if blocked:
        log.info("findings blocked on money", extra={"reasons": blocked})
    assets = await segment_repo.load_assets(ctx.db, org_id, ad_account_id)
    units = {u.sha256: u for u in (unit_from_json(x) for x in (result.get("units") or []))}
    cards, empty = card_mod.build(candidates, assets, units, limit=MAX_CARDS)
    if not cards:
        return [], empty
    await ledger_repo.record(ctx.db, org_id, ad_account_id, cards)
    backs = await _read_back(ctx.db, org_id, ad_account_id, result, assets)
    paused = await schedule_paused(ctx.db, NARRATION_SCHEDULE)
    paths = {} if paused else await segment_repo.load_asset_paths(ctx.db, org_id, ad_account_id)
    out = []
    for c in cards:
        written = None if paused else await _narrate(ctx, org_id, c, paths)
        out.append(card_mod.to_payload(c, written, backs.get(c.key)))
    return out, None


async def _narrate(ctx: JobContext, org_id: UUID, card, paths: dict):
    """Describe the footage the card is about, write it, then try to kill it.

    The clip is only worth paying for when the card is about watching: a claim or a cut rate
    is not explained by three seconds of video, and `Card.moment` is exactly the flag for
    "the curve could place this one in time".

    Every failure is survivable: the card keeps its template wording. A paid call is never
    retried (`bd19f3b`), and one unreadable file must not cost the account its plan.
    """
    narrator = narrate.Narrator(ctx.settings)
    clips: dict[str, dict] = {}
    if card.moment is not None:
        targets = [("this file", card.footage_id)]
        targets += [(f"reference {i}", r.footage_id) for i, r in enumerate(card.refs[:2], start=1)]
        span = float(card.moment.splice_at)
        for who, fid in targets[: narrate.MAX_CLIPS_PER_RUN]:
            path = paths.get(fid)
            if path is None:
                continue
            try:
                seg = await clip.cut(await ctx.blobs.get(path), 0.0, span)
                clips[who] = await narrator.describe(ctx.db, org_id, fid, seg, 0.0, span)
            except Exception:  # noqa: BLE001 - a missing clip costs a sentence, not the card
                log.warning("clip failed", extra={"footage_id": fid}, exc_info=True)
    try:
        out = await narrator.narrate(ctx.db, org_id, card, clips)
    except Exception:  # noqa: BLE001
        log.exception("narration failed", extra={"card": card.key})
        return None
    if isinstance(out, narrate.Refusal):
        log.info("card kept template wording", extra={"card": card.key, "stage": out.stage})
        return None
    return out


async def _read_back(ctx_db, org_id: UUID, ad_account_id: UUID, result: dict, assets) -> dict:
    """What became of the cards this account has already been given (docs §5 stage 7).

    Both questions are answered from the latest account read's units, which is what makes the
    read-back work for a claim or a cut rate and not only for a recut.
    """
    from cs.insights.account_read.types import unit_from_json

    units = {u.sha256: u for u in (unit_from_json(x) for x in (result.get("units") or []))}
    spend = {a.sha256: a.spend_30d for a in assets}
    first_day = {a.sha256: a.first_day for a in assets}
    sha_of = {a.footage_id: a.sha256 for a in assets}
    today = datetime.now(UTC).date()
    out = {}
    for row in await ledger_repo.open_cards(ctx_db, org_id, ad_account_id):
        base = ledger_repo.baseline_from(row)
        issued = row["first_seen_at"].date()
        sha = sha_of.get(row["spec"].get("footage_id") or "", "")
        rb = ledger.read_back(
            base,
            ledger_repo.now_from(units.get(sha), spend.get(sha, 0.0), base),
            ledger_repo.adoption_from(units, first_day, base, issued, exclude=sha),
            issued,
            today,
        )
        out[row["key"]] = rb
        status = ledger.next_status(rb.outcome)
        if status != row["status"]:
            await ledger_repo.set_status(ctx_db, ad_account_id, row["key"], status)
    return out


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
        plan.recut, plan.recut_empty_reason = await _cards(
            ctx, org_id, ad_account_id, result, run.get("read")
        )
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
            "recut": len(plan.recut),
        },
    )


def _items(payload: dict) -> list[tuple[str, str, dict]]:
    """The allocation rows, for the ledger. Recut cards write themselves there through
    `ledger_repo.record`, which also freezes the baseline they will be read back against."""
    out: list[tuple[str, str, dict]] = []
    for row in payload.get("allocate") or []:
        out.append((row["key"], row["kind"], {"creative_id": row["creative_id"]}))
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
