"""Make: one place to turn a video that works into new versions of it.

The list joins the two reasons a video is worth working on (a first-week winner, or a
converting video with a weak opening) with the work already started on it (briefs and
generated openings). The video page gathers that video's numbers, curve, brief, renders and
generations; editing and running still go through the briefs and generate routes.
"""

from __future__ import annotations

import copy
import re
import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db, get_settings_dep
from cs.api.routers import briefs as briefs_router
from cs.api.routers import generate as generate_router
from cs.briefs import facts
from cs.briefs import findings as findings_mod
from cs.core import ids
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.experiments import tag_clusters as tc
from cs.make import batch as batch_mod
from cs.make import verdict as verdict_mod
from cs.make.reconcile import reconcile

router = APIRouter(prefix="/make")
CLUSTER_MIN_VIDEOS = 4  # other videos a cluster needs before it is the verdict's baseline
WORN = 1.25  # last-30-day cost per purchase this far above the first week counts as worn out
# The two rankings behind every Make page read the whole account's delivery (0.3-0.6 s each);
# delivery only changes when the nightly sync runs, so each is kept for five minutes.
RANKING_TTL_S = 300
_RANKINGS: dict[tuple, tuple[float, object]] = {}


async def _cached(key: tuple, make):
    hit = _RANKINGS.get(key)
    if hit and time.monotonic() - hit[0] < RANKING_TTL_S:
        return copy.deepcopy(hit[1])
    value = await make()
    _RANKINGS[key] = (time.monotonic(), copy.deepcopy(value))
    return value


async def _winners(db: Database, org_id: UUID, ad_account_id: UUID) -> list[dict]:
    return await _cached(
        ("winners", org_id, ad_account_id), lambda: facts.candidates(db, org_id, ad_account_id)
    )


async def _hooks(db: Database, auth: AuthContext, ad_account_id: UUID) -> dict:
    return await _cached(
        ("hooks", auth.org_id, ad_account_id),
        lambda: generate_router.candidates(ad_account_id, auth, db),
    )


async def _owned_account(db: Database, org_id: UUID, ad_account_id: UUID) -> None:
    if not await db.fetchval(
        "SELECT 1 FROM ad_account WHERE id=$1 AND org_id=$2", ad_account_id, org_id
    ):
        raise HTTPException(404, "ad account not found")


async def _media(db: Database, blobs: BlobStore, footage_ids: list[UUID]) -> dict[UUID, dict]:
    rows = await db.fetch(
        """
        SELECT f.id, f.duration_s, f.rep_creative_id, a.path AS video_path,
               (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id AND t.kind = 'thumbnail'
                 ORDER BY t.seq LIMIT 1) AS poster_path,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name
        FROM footage f LEFT JOIN creative c ON c.id = f.rep_creative_id LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE f.id = ANY($1::uuid[])
        """,
        footage_ids,
    )
    return {
        r["id"]: {
            "name": r["name"] or "(unnamed)",
            "duration_s": float(r["duration_s"] or 0),
            "rep_creative_id": str(r["rep_creative_id"]) if r["rep_creative_id"] else None,
            "poster_url": await blobs.url_for(r["poster_path"]) if r["poster_path"] else None,
            "video_path": r["video_path"],
        }
        for r in rows
    }


@router.get("")
async def overview(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    await _owned_account(db, auth.org_id, ad_account_id)
    await reconcile(db, auth.org_id)
    winners = {c["footage_id"]: c for c in await _winners(db, auth.org_id, ad_account_id)}
    hooks = await _hooks(db, auth, ad_account_id)
    by_hook = {c["footage_id"]: c for c in hooks["candidates"]}
    work = await db.fetch(
        """
        SELECT footage_id, max(updated) AS updated, sum(briefs)::int AS briefs, sum(gens)::int AS generations,
               sum(running)::int AS running
        FROM (
          SELECT footage_id, updated_at AS updated, 1 AS briefs, 0 AS gens,
                 (status IN ('queued','running'))::int AS running
          FROM creative_brief WHERE org_id=$1 AND ad_account_id=$2
          UNION ALL
          SELECT footage_id, coalesce(finished_at, created_at), 0, 1, (status IN ('queued','running'))::int
          FROM generation WHERE org_id=$1 AND ad_account_id=$2
        ) x GROUP BY footage_id ORDER BY max(updated) DESC
        """,
        auth.org_id,
        ad_account_id,
    )
    renders = {
        r["footage_id"]: r["n"]
        for r in await db.fetch(
            "SELECT b.footage_id, count(DISTINCT r.code)::int AS n FROM brief_render r JOIN creative_brief b ON b.id = r.brief_id"
            " WHERE b.org_id=$1 AND b.ad_account_id=$2 AND r.status='done' GROUP BY b.footage_id",
            auth.org_id,
            ad_account_id,
        )
    }
    started = {str(r["footage_id"]) for r in work}

    def reasons(fid: str) -> list[str]:
        out = []
        w, h = winners.get(fid), by_hook.get(fid)
        if w:
            worn = w["cpa_30d"] is not None and w["cpa_30d"] >= w["cpa_7d"] * WORN
            out.append("winner_worn" if worn else "winner")
        if h and h["weak_hook"] and h["converting"]:
            out.append("weak_opening")
        return out

    def numbers(fid: str) -> dict[str, Any]:
        w, h = winners.get(fid) or {}, by_hook.get(fid) or {}
        return {
            "cpa_7d": w.get("cpa_7d"),
            "purchases_7d": w.get("purchases_7d"),
            "roas_7d": w.get("roas_7d"),
            "cpa_30d": w.get("cpa_30d") if w else h.get("cpa_30d"),
            "spend_30d": h.get("spend_30d"),
            "hook_rate": h.get("hook_rate"),
            "hook_vs_account": h.get("hook_vs_account"),
        }

    worth = [fid for fid in {*winners, *by_hook} if fid not in started and reasons(fid)]
    worth.sort(
        key=lambda f: (
            -(
                (by_hook.get(f) or {}).get("spend_30d")
                or (winners.get(f) or {}).get("spend_7d")
                or 0
            )
        )
    )
    ids = [UUID(f) for f in [*started, *worth]]
    media = await _media(db, blobs, ids)

    def card(fid: str) -> dict:
        m = media.get(UUID(fid), {})
        return {
            "footage_id": fid,
            "reasons": reasons(fid),
            **numbers(fid),
            **{k: v for k, v in m.items() if k != "video_path"},
        }

    # Stage of each video in progress: ready once a drafted batch has variants and all of them
    # can launch, drafting otherwise. A video whose batch was launched and has nothing new in
    # draft leaves Make; its test is under Tests.
    stages: dict[str, str] = {}
    batch_rows = await db.fetch(
        """
        SELECT footage_id, bool_or(status = 'launched') AS launched,
               bool_or(status = 'drafting' AND EXISTS (
                 SELECT 1 FROM make_batch_item i WHERE i.batch_id = make_batch.id AND i.decision = 'in')) AS drafting_in
        FROM make_batch WHERE org_id=$1 AND ad_account_id=$2 GROUP BY footage_id
        """,
        auth.org_id,
        ad_account_id,
    )
    for r in batch_rows:
        fid = str(r["footage_id"])
        if r["drafting_in"]:
            v = await video(r["footage_id"], auth, db, blobs)
            stages[fid] = "ready" if v["batch"]["can_launch"] else "drafting"
        elif r["launched"]:
            stages[fid] = "launched"
    for fid in started:
        stages.setdefault(fid, "drafting")
    extra_ids = [UUID(f) for f in stages if UUID(f) not in media]
    if extra_ids:
        media.update(await _media(db, blobs, extra_ids))
    progress_rows = {str(r["footage_id"]): r for r in work}
    by_stage: dict[str, list[dict]] = {"drafting": [], "ready": []}
    for fid, st in stages.items():
        if st == "launched":
            continue
        r = progress_rows.get(fid)
        by_stage[st].append(
            {
                **card(fid),
                "briefs": r["briefs"] if r else 0,
                "generations": r["generations"] if r else 0,
                "renders": renders.get(UUID(fid), 0),
                "running": bool(r and r["running"] > 0),
                "updated_at": r["updated"].isoformat() if r and r["updated"] else None,
            }
        )
    return {
        "stages": by_stage,
        "account_hook_rate": hooks["account_hook_rate"],
        "account_cpa": hooks["account_cpa"],
        "in_progress": [
            {
                **card(str(r["footage_id"])),
                "briefs": r["briefs"],
                "generations": r["generations"],
                "renders": renders.get(r["footage_id"], 0),
                "running": r["running"] > 0,
                "updated_at": r["updated"].isoformat() if r["updated"] else None,
            }
            for r in work
        ],
        "worth_working_on": [card(f) for f in worth[:24]],
    }


@router.get("/{footage_id}")
async def video(
    footage_id: UUID,
    finding: str | None = None,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    f = await db.fetchrow(
        "SELECT ad_account_id FROM footage WHERE id=$1 AND org_id=$2 AND format='video'",
        footage_id,
        auth.org_id,
    )
    if f is None:
        raise HTTPException(404)
    await reconcile(db, auth.org_id)
    acc = f["ad_account_id"]
    m = (await _media(db, blobs, [footage_id]))[footage_id]
    winner = next(
        (c for c in await _winners(db, auth.org_id, acc) if c["footage_id"] == str(footage_id)),
        None,
    )
    hooks = await _hooks(db, auth, acc)
    hook = next((c for c in hooks["candidates"] if c["footage_id"] == str(footage_id)), None)
    if hook is None:
        # Not among the top spenders: read its own last-30-day window.
        w = await db.fetchrow(
            "SELECT spend, impressions, views_3s, purchases FROM footage_metric_window WHERE footage_id=$1 AND window_kind='last_30d'",
            footage_id,
        )
        if w and w["impressions"]:
            rate = w["views_3s"] / w["impressions"]
            acct = hooks["account_hook_rate"]
            hook = {
                "spend_30d": round(float(w["spend"]), 2),
                "cpa_30d": round(float(w["spend"]) / w["purchases"], 2) if w["purchases"] else None,
                "hook_rate": round(rate, 4),
                "hook_vs_account": round(rate / acct - 1, 3) if acct else None,
                "weak_hook": bool(acct and rate <= acct * generate_router.WEAK_HOOK),
                "converting": False,
            }
    curve = [
        [float(r["start_s"]), round(float(r["share"]), 4)]
        for r in await db.fetch(
            "SELECT start_s, share FROM play_curve_point WHERE footage_id=$1 AND window_kind='lifetime' ORDER BY bucket",
            footage_id,
        )
    ]
    brief_id = await db.fetchval(
        "SELECT id FROM creative_brief WHERE footage_id=$1 AND org_id=$2 ORDER BY created_at DESC LIMIT 1",
        footage_id,
        auth.org_id,
    )
    brief = await briefs_router.get_brief(brief_id, auth, db, blobs) if brief_id else None
    gen_ids = await db.fetch(
        "SELECT id FROM generation WHERE footage_id=$1 AND org_id=$2 ORDER BY created_at DESC",
        footage_id,
        auth.org_id,
    )
    gens = [await generate_router.get_generation(r["id"], auth, db, blobs) for r in gen_ids]
    worn = bool(
        winner and winner["cpa_30d"] is not None and winner["cpa_30d"] >= winner["cpa_7d"] * WORN
    )
    # The opening's strength is the verdict's job here (against its cluster), not a chip's.
    reasons = ["winner_worn" if worn else "winner"] if winner else []
    draft = await batch_mod.drafting_batch(db, auth.org_id, acc, footage_id)
    gen_paths = {
        str(r["id"]): r
        for r in await db.fetch(
            "SELECT id, result_path, resolution FROM generation WHERE footage_id=$1", footage_id
        )
    }
    for g in gens:
        g["result_path"] = gen_paths.get(g["id"], {}).get("result_path")
    idea_list = batch_mod.ideas(
        brief, gens, await batch_mod.decisions(db, draft["id"] if draft else None)
    )
    for i in idea_list:
        i.pop("media_path", None)
    cpa = (winner or {}).get("cpa_7d") or (hook or {}).get("cpa_30d")
    launched = await db.fetch(
        "SELECT b.id, b.experiment_id, b.launched_at, e.title FROM make_batch b LEFT JOIN experiment e ON e.id = b.experiment_id"
        " WHERE b.footage_id=$1 AND b.org_id=$2 AND b.status='launched' ORDER BY b.launched_at DESC",
        footage_id,
        auth.org_id,
    )
    # The verdict, the timeline and where each idea changes the video. Judged against its
    # cluster (like for like) and against the account; the cluster leads when it can.
    worn = bool(winner and winner.get("cpa_30d") and winner["cpa_30d"] >= winner["cpa_7d"] * WORN)
    mine = await verdict_mod.video_rates(db, footage_id)
    acct = await _cached(
        ("acct_rates", auth.org_id, acc), lambda: verdict_mod.account_rates(db, auth.org_id, acc)
    )
    members = await _cached(("clusters", auth.org_id), lambda: findings_mod.cluster_members(db, auth.org_id))
    cluster = members.get(footage_id)
    vs_account = verdict_mod.verdict(mine, verdict_mod.without(acct, mine), worn=worn)
    vs_account["baseline"] = {"kind": "account", "label": "the account", "videos": acct.get("videos", 0) - (1 if mine else 0)}
    vs_cluster = None
    if cluster and cluster != tc.OTHER:
        ids = [f for f, c in members.items() if c == cluster]
        grp = await _cached(
            ("cluster_rates", auth.org_id, acc, cluster),
            lambda: verdict_mod.group_rates(db, auth.org_id, acc, ids),
        )
        base = verdict_mod.without(grp, mine)
        if base["videos"] >= CLUSTER_MIN_VIDEOS and base["impressions"] >= verdict_mod.MIN_IMPRESSIONS:
            vs_cluster = verdict_mod.verdict(mine, base, worn=worn)
            vs_cluster["baseline"] = {"kind": "cluster", "label": cluster, "videos": base["videos"]}
    verdict = vs_cluster or vs_account
    verdict["other"] = vs_account if vs_cluster else None
    verdict["cluster"] = cluster if cluster != tc.OTHER else None
    duration = float(m.get("duration_s") or 0)
    for i in idea_list:
        ch = batch_mod.changes(i, duration) if duration else None
        i["span"] = ch
        part = ch["part"] if ch else None
        i["part"] = part
        i["aim"] = (
            "weak"
            if part and part == verdict["weak"]
            else "strong"
            if part in verdict["strong"]
            else "even"
            if part
            else None
        )
    focus = None
    fid = finding or ((brief or {}).get("focus") or {}).get("id")
    if fid:
        f_all = await findings_mod.all_findings(db, auth.org_id)
        placed = await _placements(db, auth.org_id)
        base = f_all.get(fid)
        if base:
            pl = placed.get(fid) or {}
            sentences = ((brief or {}).get("facts") or {}).get("sentences") or []
            focus = {
                **base,
                "trait": pl.get("trait"),
                "direction": pl.get("direction"),
                "tier": pl.get("tier"),
                "found": batch_mod.locate(pl.get("trait") or base["title"], sentences)
                if sentences
                else None,
                "applied": (brief or {}).get("focus", {})
                and (brief or {}).get("focus", {}).get("id") == fid,
            }
    sentences = ((brief or {}).get("facts") or {}).get("sentences") or []
    timeline = {
        "duration": duration,
        "sentences": [{"start": x["start"], "end": x["end"], "text": x["text"]} for x in sentences],
        "parts": {
            "opening": [0, min(batch_mod.OPENING_S, duration * 0.2)],
            "middle": [
                min(batch_mod.OPENING_S, duration * 0.2),
                duration * batch_mod.CLOSE_FRACTION,
            ],
            "close": [duration * batch_mod.CLOSE_FRACTION, duration],
        },
    }
    return {
        "verdict": verdict,
        "timeline": timeline,
        "focus": focus,
        "ideas": idea_list,
        "batch": batch_mod.batch_view(idea_list, draft, cpa),
        "launched": [
            {
                "experiment_id": str(r["experiment_id"]) if r["experiment_id"] else None,
                "title": r["title"],
                "launched_at": r["launched_at"].isoformat() if r["launched_at"] else None,
            }
            for r in launched
        ],
        "footage_id": str(footage_id),
        "ad_account_id": str(acc),
        **{k: v for k, v in m.items() if k != "video_path"},
        "video_url": await blobs.url_for(m["video_path"]) if m.get("video_path") else None,
        "reasons": reasons,
        "winner": winner,
        "hook": hook,
        "account_hook_rate": hooks["account_hook_rate"],
        "account_cpa": hooks["account_cpa"],
        "curve": curve,
        "steepest_drop": facts.steepest_drop([(t, v) for t, v in curve]) if curve else None,
        "brief": brief,
        "generations": gens,
    }


class DecideBody(BaseModel):
    ref: str
    decision: str  # 'in', 'skipped', or 'none' to clear


@router.post("/{footage_id}/decide")
async def decide(
    footage_id: UUID,
    body: DecideBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Put an idea in the batch, skip it, or clear the decision."""
    if body.decision not in ("in", "skipped", "none"):
        raise HTTPException(422, "decision must be in, skipped or none")
    f = await db.fetchrow(
        "SELECT ad_account_id FROM footage WHERE id=$1 AND org_id=$2", footage_id, auth.org_id
    )
    if f is None:
        raise HTTPException(404)
    kind, _, rest = body.ref.partition(":")
    if kind == "brief":
        bid = rest.split(":", 1)[0]
        ok = await db.fetchval(
            "SELECT 1 FROM creative_brief WHERE id=$1::uuid AND footage_id=$2 AND org_id=$3",
            bid,
            footage_id,
            auth.org_id,
        )
    elif kind == "gen":
        ok = await db.fetchval(
            "SELECT 1 FROM generation WHERE id=$1::uuid AND footage_id=$2 AND org_id=$3",
            rest,
            footage_id,
            auth.org_id,
        )
    else:
        ok = None
    if not ok:
        raise HTTPException(404, "that idea is not on this video")
    b = await batch_mod.drafting_batch(db, auth.org_id, f["ad_account_id"], footage_id, create=True)
    if body.decision == "none":
        await db.execute(
            "DELETE FROM make_batch_item WHERE batch_id=$1 AND ref=$2", UUID(b["id"]), body.ref
        )
        return {"ok": True}
    if body.decision == "in":
        n = await db.fetchval(
            "SELECT count(*) FROM make_batch_item WHERE batch_id=$1 AND decision='in' AND ref <> $2",
            UUID(b["id"]),
            body.ref,
        )
        if n >= batch_mod.MAX_IN_BATCH:
            raise HTTPException(
                409, f"A batch holds {batch_mod.MAX_IN_BATCH} variants. Take one out first."
            )
    await db.execute(
        "INSERT INTO make_batch_item (batch_id, ref, decision) VALUES ($1, $2, $3)"
        " ON CONFLICT (batch_id, ref) DO UPDATE SET decision = EXCLUDED.decision, decided_at = now()",
        UUID(b["id"]),
        body.ref,
        body.decision,
    )
    return {"ok": True}


@router.post("/{footage_id}/launch")
async def launch(
    footage_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The person has uploaded the batch to Meta: record it as a test under Tests, with the
    original as control and every variant file attached."""
    v = await video(footage_id, auth, db, blobs)
    b = v["batch"]
    if not b["id"] or not b["can_launch"]:
        raise HTTPException(
            409,
            "The batch is not ready: every variant in it must be rendered and free of blockers.",
        )
    chosen = [i for i in v["ideas"] if i["ref"] in b["chosen"]]
    paths: dict[str, str | None] = {}
    for i in chosen:
        if i["kind"] == "opening":
            paths[i["ref"]] = await db.fetchval(
                "SELECT result_path FROM generation WHERE id=$1::uuid", i["generation_id"]
            )
        else:
            paths[i["ref"]] = await db.fetchval(
                "SELECT path FROM brief_render WHERE brief_id=$1::uuid AND code=$2 AND status='done' ORDER BY created_at DESC LIMIT 1",
                i["brief_id"],
                i["code"],
            )
    title = f"{v['name'].split('_')[-1] if '_' in v['name'] else v['name']}: {len(chosen)} new versions against the original"
    eid = ids.new_id()
    steps = [
        (
            "launch",
            "Launch",
            "Upload the variants and the original to one new TESTING ad set, same day, equal budget.",
            "done",
            None,
            "Marked launched from Make.",
        ),
        (
            "read-openings",
            "Read the openings",
            f"After about {batch_mod.HOOK_IMPRESSIONS:,} impressions each, compare the share still watching at 3s with the original's.",
            "pending",
            None,
            None,
        ),
        (
            "read-purchases",
            "Read cost per purchase",
            f"Once each ad has about {batch_mod.PURCHASES_PER_AD} purchases, compare cost per purchase with the original's.",
            "pending",
            None,
            None,
        ),
    ]
    media = [
        {"title": f"{i['code']}: {i['title']}", "note": i["change"] or "", "path": paths[i["ref"]]}
        for i in chosen
        if paths.get(i["ref"])
    ]
    async with db.transaction() as tx:
        await tx.execute(
            "INSERT INTO experiment (id, org_id, slug, title, question, method, rules, kind)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'test')",
            eid,
            auth.org_id,
            f"batch-{b['id'][:8]}",
            title,
            "Does any of these versions beat the original on the step it was made to change?",
            "Each variant runs against the original in one TESTING ad set. Openings are judged on the share still watching "
            "at 3s; the rest on cost per purchase.",
            [
                {
                    "title": i["code"],
                    "rule": i["change"] or i["title"],
                    "why": i["finding"] or "From the brief.",
                    "effect": i["read_on"] or "",
                    "dropped": [],
                    "decided_by": "you",
                }
                for i in chosen
            ],
        )
        for n, (key, st_title, detail, status, waiting, note) in enumerate(steps, 1):
            await tx.execute(
                "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail, status, waiting_on, result_note,"
                " progress_done, progress_total, cost_usd, finished_at)"
                " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9,0, CASE WHEN $6='done' THEN now() END)",
                eid,
                n,
                key,
                st_title,
                detail,
                status,
                waiting,
                note,
                1 if status == "done" else 0,
            )
        await tx.execute(
            "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source) VALUES ($1, $2, 'parent', 'you')",
            eid,
            footage_id,
        )
        await tx.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1, 'media', $2)",
            eid,
            {"videos": media},
        )
        await tx.execute(
            "UPDATE make_batch SET status='launched', experiment_id=$2, launched_at=now() WHERE id=$1",
            UUID(b["id"]),
            eid,
        )
    return {"experiment_id": str(eid)}


@router.get("/from-creative/{creative_id}")
async def footage_of(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    """The video behind an ad, so an ad's page can open its Make page."""
    fid = await db.fetchval(
        "SELECT fc.footage_id FROM footage_creative fc JOIN footage f ON f.id = fc.footage_id"
        " WHERE fc.creative_id=$1 AND f.org_id=$2 AND f.format='video' LIMIT 1",
        creative_id,
        auth.org_id,
    )
    if fid is None:
        raise HTTPException(404, "this ad has no video file to make versions of")
    return {"footage_id": str(fid)}


async def _placements(db: Database, org_id: UUID) -> dict:
    """The playbook's placement of each finding: its trait, direction and tier."""
    p = await db.fetchval(
        "SELECT r.payload->'findings' FROM experiment_result r JOIN experiment e ON e.id = r.experiment_id"
        " WHERE e.org_id=$1 AND e.slug='tag-clusters' AND r.key='playbook'",
        org_id,
    )
    return p or {}


class FindingEditBody(BaseModel):
    finding_id: str


READ_ON = {"stop": "Share still watching at 3s", "click": "Click rate", "buy": "Purchase rate"}


@router.post("/{footage_id}/finding-edit")
async def finding_edit(
    footage_id: UUID,
    body: FindingEditBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Build the edit a finding asks for, in code: find the line in this video that carries the
    trait and cut it. No model call; a video without a brief gets its line timings first (one
    transcription, under a cent). Only findings that say to avoid something can be cut; adding
    something new goes through the brief."""
    f = await db.fetchrow(
        "SELECT ad_account_id FROM footage WHERE id=$1 AND org_id=$2 AND format='video'",
        footage_id,
        auth.org_id,
    )
    if f is None:
        raise HTTPException(404)
    base = (await findings_mod.all_findings(db, auth.org_id)).get(body.finding_id)
    pl = (await _placements(db, auth.org_id)).get(body.finding_id)
    if base is None or pl is None:
        raise HTTPException(404, "finding not found")
    if pl["direction"] != "avoid":
        raise HTTPException(
            422,
            "This finding asks to add something, which needs new material. Write variants around it instead.",
        )
    row = await db.fetchrow(
        "SELECT id, facts, content, version FROM creative_brief WHERE footage_id=$1 AND org_id=$2 AND status='done'"
        " ORDER BY created_at DESC LIMIT 1",
        footage_id,
        auth.org_id,
    )
    if row is None:
        fx = await facts.parent_facts(
            db, blobs, auth.org_id, f["ad_account_id"], footage_id, settings
        )
        bid = ids.new_id()
        content = {
            "title": "",
            "slots": [],
            "findings": [],
            "batches": [],
            "script": {"name": "", "note": "", "lines": []},
            "launch": [],
            "claims": [],
            "offer": "[Current offer]",
            "dropped": [],
        }
        await db.execute(
            "INSERT INTO creative_brief (id, org_id, ad_account_id, footage_id, title, status, facts, content, focus, created_by)"
            " VALUES ($1,$2,$3,$4,$5,'done',$6,$7,$8,$9)",
            bid,
            auth.org_id,
            f["ad_account_id"],
            footage_id,
            base["title"],
            fx,
            content,
            {**base, "trait": pl["trait"]},
            auth.user_id,
        )
        row = await db.fetchrow(
            "SELECT id, facts, content, version FROM creative_brief WHERE id=$1", bid
        )
    fx, content = row["facts"], row["content"]
    S = fx.get("sentences") or []
    hits = batch_mod.locate(pl["trait"], S)
    if not hits:
        raise HTTPException(
            422,
            "Could not find a line carrying this in the video. Write variants around it instead.",
        )
    drop = {S.index(h) for h in hits}
    if len(drop) == len(S):
        raise HTTPException(422, "That would cut the whole video.")
    segments, run = [], None
    for k in range(len(S)):
        if k in drop:
            if run:
                segments.append([S[run[0]]["cut_before"], S[run[1]]["cut_after"]])
                run = None
            continue
        run = (run[0], k) if run else (k, k)
    if run:
        segments.append([S[run[0]]["cut_before"], S[run[1]]["cut_after"]])
    # The last line's cut runs to the end of the file; when it is cut, keep what follows the
    # speech (the end card), or the edit would drop the product and the call to buy.
    duration = float(fx.get("duration_s") or 0)
    if (len(S) - 1) in drop and duration - S[-1]["end"] > 0.5:
        segments.append([round(S[-1]["end"] + 0.1, 2), duration])
    tight = [S[k]["cut_before"] for k in drop if not S[k].get("clean_before", True)] + [
        S[k]["cut_after"] for k in drop if not S[k].get("clean_after", True)
    ]
    existing = [v["code"] for b in content.get("batches") or [] for v in b.get("variants") or []]
    code = f"F{sum(1 for c in existing if c.startswith('F')) + 1}"
    choice = (
        re.sub(
            r"[^A-Za-z0-9]+",
            " ",
            (await _choice_name(db, auth.org_id, pl.get("choice_key"))) or "Finding",
        )
        .title()
        .replace(" ", "")
    )
    prefix = "_".join((fx.get("name") or "Video").split("_")[:2])
    quoted = "; ".join(
        f"“{h['text']}” ({int(h['start'] // 60)}:{int(h['start'] % 60):02d})" for h in hits
    )
    variant = {
        "code": code,
        "kind": "cut",
        "name": f"{prefix}-{code}_Remove{choice}",
        "change": f"Cut {quoted}. From What works: {base['title']}.",
        "read_on": READ_ON.get(_step_of(base), "Purchase rate"),
        "edit": {"segments": segments, "overlays": [], "hold_end": 0, "tight": tight},
    }
    batches = content.get("batches") or []
    fw = next((b for b in batches if b.get("title") == "From What works"), None)
    if fw is None:
        fw = {"title": "From What works", "variants": []}
        batches.insert(0, fw)
    fw["variants"].append(variant)
    content["batches"] = batches
    await db.execute(
        "UPDATE creative_brief SET content=$2, version=version+1, updated_at=now() WHERE id=$1",
        row["id"],
        content,
    )
    return {"brief_id": str(row["id"]), "code": code, "cut": [h["text"] for h in hits]}


async def _choice_name(db: Database, org_id: UUID, key: str | None) -> str | None:
    if not key:
        return None
    return await db.fetchval(
        "SELECT r.payload->'choices'->$2->>'name' FROM experiment_result r JOIN experiment e ON e.id = r.experiment_id"
        " WHERE e.org_id=$1 AND e.slug='tag-clusters' AND r.key='playbook'",
        org_id,
        key,
    )


def _step_of(f: dict) -> str | None:
    for n in f.get("numbers") or []:
        first = (n.get("label") or "").strip().lower()
        if first.startswith(("buy", "convert", "purchase")):
            return "buy"
    for n in f.get("numbers") or []:
        first = (n.get("label") or "").strip().lower()
        if first.startswith("click"):
            return "click"
        if first.startswith(("stop", "hook")):
            return "stop"
    return None
