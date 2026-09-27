"""What works: findings that compare like with like.

Every finding here comes from inside one cluster (who carries the video and when the product
first appears). Account-wide comparisons mixed format with content: when the Insights tab's 11
ideas were re-checked within clusters (2026-09-25), 4 turned out to be the format and none held
strongly, so they are not shown here. A cluster's findings are its reviewed profile read
(scripts/review_profile_read.py), each already checked against the data.
"""

from __future__ import annotations

import copy
import re
import time
from uuid import UUID

import openai
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db, get_settings_dep
from cs.api.routers import experiments as ex
from cs.briefs import findings as findings_mod
from cs.core import queue
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.experiments import creator_script
from cs.experiments import tag_clusters as tc
from cs.make import script_video as sv
from cs.make import seedance as sd_mod
from cs.make import test_read
from cs.make.reconcile import reconcile

router = APIRouter(prefix="/what-works")
SLUG = "tag-clusters"
MIN_SPEND = 200
WINDOW = "last_30d"
ORDER = {"holds": 0, "lead": 1, "context": 2}
# The cluster part changes only when a cluster is re-read (an offline script), so it is kept
# for a few minutes per org; progress on each finding is read fresh on every request.
CACHE_S = 300
_CACHE: dict = {}


async def _clusters(db: Database, blobs: BlobStore, auth: AuthContext, eid) -> dict:
    view = await ex.experiment_themes(eid, "cluster", MIN_SPEND, WINDOW, auth, db, blobs)
    total = sum(g["totals"]["spend"] for g in view["groups"]) or 1
    # Only the parts of each stored read this page uses: the full payload carries the model's
    # whole output and tables, and fetching it made this page 1.6 s slower (2026-09-26).
    reads = {
        r["key"].split(":", 1)[1]: r
        for r in await db.fetch(
            "SELECT key, payload->'reviewed' AS reviewed, payload->'videos' AS videos, created_at"
            " FROM experiment_result WHERE experiment_id=$1 AND key LIKE 'profile_read:%'",
            eid,
        )
    }
    wanted = {v["id"] for r in reads.values() for v in (r["videos"] or {}).values()}
    extra = {
        str(r["id"]): r
        for r in await db.fetch(
            """
            SELECT f.id, f.rep_creative_id,
                   (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id
                     AND t.kind = 'thumbnail' ORDER BY t.seq LIMIT 1) AS poster
            FROM footage f WHERE f.id = ANY($1::uuid[])
            """,
            list(wanted),
        )
    }
    out = []
    for g in view["groups"]:
        t = g["totals"]
        r = reads.get(g["key"])
        findings = sorted(
            (r["reviewed"] if r else None) or [], key=lambda f: ORDER.get(f.get("strength"), 9)
        )
        raw_videos = (r["videos"] if r else None) or {}
        videos = {}
        for code, v in raw_videos.items():
            e = extra.get(v["id"])
            if e is None:
                continue
            videos[code] = {
                "footage_id": v["id"],
                "name": v.get("name") or code,
                "poster_url": await blobs.url_for(e["poster"]) if e["poster"] else None,
                "rep_creative_id": str(e["rep_creative_id"]) if e["rep_creative_id"] else None,
                "spend": v.get("spend"),
                "cpa": v.get("cpa"),
                "side": v.get("side"),
            }
        read = {"created_at": r["created_at"].isoformat() if r else None}
        out.append(
            {
                "key": g["key"],
                "name": g["name"],
                "files": t["files"],
                "spend": round(t["spend"], 2),
                "share": round(t["spend"] / total, 3),
                "purchases": t["purchases"],
                "cpa": round(t["cpa"], 2) if t.get("cpa") else None,
                "hook_rate": round(t["hook_rate"], 4) if t.get("hook_rate") is not None else None,
                "read_at": read["created_at"],
                "read_videos": videos,
                "videos": [
                    {
                        "footage_id": x["id"],
                        "name": x["name"],
                        "poster_url": x.get("poster_url"),
                        "spend": x.get("spend"),
                    }
                    for x in sorted(g.get("files") or [], key=lambda x: -(x.get("spend") or 0))[:4]
                ],
                "findings": [
                    {
                        "id": findings_mod.finding_id(g["key"], f["title"]),
                        "title": f["title"],
                        "body": f["body"],
                        "strength": f.get("strength"),
                        "basis": f.get("basis"),
                        "numbers": f.get("numbers") or [],
                        "examples": [
                            {"code": c, **videos[c]} for c in (f.get("videos") or []) if c in videos
                        ][:3],
                    }
                    for f in findings
                ],
            }
        )
    out.sort(key=lambda c: -c["spend"])
    return {"window": WINDOW, "span": view.get("span"), "min_spend": MIN_SPEND, "clusters": out}


@router.get("")
async def what_works(
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    eid = await db.fetchval(
        "SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", auth.org_id, SLUG
    )
    if eid is None:
        raise HTTPException(404, "no clusters for this account yet")
    cached = _CACHE.get(auth.org_id)
    if cached and time.monotonic() - cached[0] < CACHE_S:
        body = copy.deepcopy(cached[1])
    else:
        body = await _clusters(db, blobs, auth, eid)
        _CACHE[auth.org_id] = (time.monotonic(), copy.deepcopy(body))
    out = body["clusters"]
    progress = await _progress(db, auth.org_id)
    tested = await test_read.by_finding(db, auth.org_id)
    for c in out:
        for f in c["findings"]:
            f["progress"] = progress.get(f["id"], [])
            f["status"] = _status(f["progress"])
            # What tests of this finding found once launched (Make → Tests → back here).
            f["tests"] = [
                {k: t.get(k) for k in ("experiment_id", "test", "title", "verdict", "measure", "p", "version", "original")}
                for t in tested.get(f["id"], [])
            ]
            f["test"] = test_read.summary(tested.get(f["id"], []))
    body["playbook"] = await _playbook(db, eid, out)
    body["creator_scripts"] = await _creator_scripts(db, eid, out, blobs, auth.org_id)
    return body


async def _creator_scripts(db: Database, eid, clusters: list[dict], blobs: BlobStore, org_id) -> list[dict]:
    """One row per kind of video (Other is a mixed bag and gets none), with its script if written
    and the latest video made from it."""
    await reconcile(db, org_id)
    have = await creator_script.stored(db, eid)
    videos = {
        r["cluster"]: r
        for r in await db.fetch(
            "SELECT DISTINCT ON (cluster) id, cluster, status, stage, error, path, checks, estimated_cost_usd,"
            " clip_cost_usd, plan, voice, product, created_at, experiment_id"
            " FROM script_video WHERE org_id=$1 ORDER BY cluster, created_at DESC",
            org_id,
        )
    }
    out = []
    for c in clusters:
        if c["key"] == tc.OTHER or not c.get("read_at"):
            continue
        script = have.get(c["key"])
        v = videos.get(c["key"])
        out.append(
            {
                "cluster": c["key"],
                "name": c["name"],
                "files": c["files"],
                "spend": c["spend"],
                "cpa": c.get("cpa"),
                "script": script,
                "makeable": sv.makeable(c["key"]),
                "estimate": sv.estimate(script, c["key"]) if script and sv.makeable(c["key"]) else None,
                "video": {
                    "id": str(v["id"]),
                    "status": v["status"],
                    "stage": v["stage"],
                    "error": v["error"],
                    "url": await blobs.url_for(v["path"]) if v["path"] and v["status"] == "done" else None,
                    "checks": v["checks"],
                    "estimated_cost_usd": float(v["estimated_cost_usd"]) if v["estimated_cost_usd"] is not None else None,
                    "clip_cost_usd": float(v["clip_cost_usd"]) if v["clip_cost_usd"] is not None else None,
                    "clip_plan": _clip_plan(v),
                    "shots": _shots(v["plan"]),
                    "prompt_text": (v["plan"] or {}).get("prompt"),
                    "mode": sv.mode(v["cluster"]),
                    "voice_url": await blobs.url_for(v["voice"]["path"]) if (v["voice"] or {}).get("path") else None,
                    "product_url": await blobs.url_for(v["product"]["path"]) if (v["product"] or {}).get("path") else None,
                    "created_at": v["created_at"].isoformat(),
                    "experiment_id": str(v["experiment_id"]) if v["experiment_id"] else None,
                }
                if v
                else None,
            }
        )
    return out


@router.post("/script-video/{video_id}/approve")
async def approve_script_video(
    video_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Approve the clip spend shown on the page and generate the clips."""
    r = await db.fetchrow(
        "UPDATE script_video SET approved_at=now(), status='queued', error=NULL, updated_at=now()"
        " WHERE id=$1 AND org_id=$2 AND status IN ('confirm','blocked') RETURNING id",
        video_id,
        auth.org_id,
    )
    if r is None:
        raise HTTPException(409, "This video is not waiting for approval.")
    await queue.enqueue(
        db, auth.org_id, sv.JOB, {"script_video_id": str(video_id)}, dedup_key=f"script_video:{video_id}:clips", max_attempts=1
    )
    return {"id": str(video_id), "status": "queued"}


def _ad_name(cluster: str, title: str | None) -> str:
    """The name the page shows next to Download (new-video.tsx builds the same one)."""
    kind = re.sub(r"[^A-Za-z0-9]+", "", re.sub(r"\s*\(.*?\)\s*", "", cluster))
    t = re.sub(r"[^A-Za-z0-9]+", "", title or "Script")
    return f"{'AIGEN_' if sv.makeable(cluster) else ''}NEW_{kind}_{t}"


@router.post("/script-video/{video_id}/launch")
async def launch_script_video(
    video_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """The person has put the new video live in Meta: record it as a test under Tests, judged
    against the other videos of its kind."""
    v = await db.fetchrow("SELECT * FROM script_video WHERE id=$1 AND org_id=$2", video_id, auth.org_id)
    if v is None:
        raise HTTPException(404)
    if v["experiment_id"]:
        return {"experiment_id": str(v["experiment_id"])}
    if v["status"] != "done" or not v["path"]:
        raise HTTPException(409, "The video is not finished yet.")
    if (v["checks"] or {}).get("too_alike"):
        raise HTTPException(409, "A shot looks like a video that already ran. Make another before launching.")
    script = v["script"]
    eid = new_id()
    title = f"New video, {v['cluster']}: {script.get('title') or 'from the script'}"
    steps = [
        ("launch", "Launch", "Put the new video live in a TESTING ad set alongside the kind's current videos.", "done", "Marked launched from Make."),
        ("read-openings", "Read the opening", "After a few thousand impressions, compare the share still watching at 3s with its kind's.", "pending", None),
        ("read-purchases", "Read cost per purchase", "Once it has about 20 purchases, compare cost per purchase with its kind's.", "pending", None),
    ]
    async with db.transaction() as tx:
        await tx.execute(
            "INSERT INTO experiment (id, org_id, slug, title, question, method, rules, kind)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, 'test')",
            eid,
            auth.org_id,
            f"new-{str(video_id)[:8]}",
            title,
            "Does a new video made from this kind's script hold up against the kind's current videos?",
            "The new video runs in a TESTING ad set; it is judged against the other videos of its kind on the opening and on cost per purchase.",
            [
                {
                    "title": script.get("title") or "New video",
                    "rule": script.get("note") or "",
                    "why": "; ".join(f["title"] for f in script.get("follows") or []) or "The kind's script.",
                    "effect": "cost per purchase",
                    "dropped": [],
                    "decided_by": "you",
                    # The same ad name the page tells the person to use, and the finding it tests.
                    "ad_name": _ad_name(v["cluster"], script.get("title")),
                    "finding_id": next((f["id"] for f in script.get("follows") or []), None),
                }
            ],
        )
        for n, (key, st_title, detail, status, note) in enumerate(steps, 1):
            await tx.execute(
                "INSERT INTO experiment_step (experiment_id, ordinal, key, title, detail, status, waiting_on, result_note,"
                " progress_done, progress_total, cost_usd, finished_at)"
                " VALUES ($1,$2,$3,$4,$5,$6,NULL,$7,$8,$8,0, CASE WHEN $6='done' THEN now() END)",
                eid,
                n,
                key,
                st_title,
                detail,
                status,
                note,
                1 if status == "done" else 0,
            )
        for fv in script.get("from_videos") or []:
            await tx.execute(
                "INSERT INTO experiment_item (experiment_id, footage_id, label, label_source) VALUES ($1, $2, 'parent', 'you')"
                " ON CONFLICT DO NOTHING",
                eid,
                UUID(fv["footage_id"]),
            )
        await tx.execute(
            "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1, 'media', $2)",
            eid,
            {"videos": [{"title": script.get("title") or "New video", "note": script.get("note") or "", "path": v["path"]}]},
        )
        await tx.execute(
            "UPDATE script_video SET experiment_id=$2, launched_at=now(), updated_at=now() WHERE id=$1", video_id, eid
        )
    return {"experiment_id": str(eid)}


def _clip_plan(v) -> dict | None:
    p = v["plan"]
    if not p:
        return None
    return sd_mod.cost(p) if "beats" in p else None  # plans from the earlier clip pipeline are planned again


def _shots(p: dict | None) -> list[dict]:
    """What the page lists before approval: each timed beat, what happens and its words."""
    if not p:
        return []
    if "beats" in p:
        return [
            {
                "prompt": f"{b['start']}-{b['end']} s · {b['action']}" + (f" “{b['line']}”" if b["line"].strip() else " (silent)"),
                "product": False,
                "lines": [],
            }
            for b in p["beats"]
        ]
    return []


class MakeBody(BaseModel):
    cluster: str


@router.post("/script-video")
async def make_script_video(
    body: MakeBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Start a video from the kind's stored script: plan the shots, voice the lines and find the
    product (a few cents), then stop at 'confirm' with the exact clip cost. A blocked video (not
    enough gateway credit) is picked up again where it stopped."""
    eid = await creator_script.experiment_id(db, auth.org_id)
    if eid is None:
        raise HTTPException(404, "no clusters for this account yet")
    if not sv.makeable(body.cluster):
        raise HTTPException(400, "This kind needs a real person on camera; a generated one would be an AI video.")
    script = (await creator_script.stored(db, eid)).get(body.cluster)
    if not script:
        raise HTTPException(400, "Write this kind's script first.")
    if script.get("blockers"):
        raise HTTPException(400, "The script breaks a rule for this kind of video: " + "; ".join(script["blockers"]) + ". Write it again first.")
    if script.get("too_close"):
        raise HTTPException(400, "The script is too close to a video that already ran. Write it again first.")
    acc = await db.fetchval(
        "SELECT ad_account_id FROM footage WHERE id=$1", UUID(script["from_videos"][0]["footage_id"])
    )
    live = await db.fetchrow(
        "SELECT id, status FROM script_video WHERE org_id=$1 AND cluster=$2 AND status IN ('queued','running','confirm','blocked')"
        " ORDER BY created_at DESC LIMIT 1",
        auth.org_id,
        body.cluster,
    )
    if live and live["status"] in ("queued", "running", "confirm"):
        return {"id": str(live["id"]), "status": live["status"]}
    if live:  # blocked on credit: pick it up again where it stopped
        vid = live["id"]
        await db.execute("UPDATE script_video SET status='queued', error=NULL, updated_at=now() WHERE id=$1", vid)
    else:
        vid = new_id()
        est = sv.estimate(script, body.cluster)
        await db.execute(
            "INSERT INTO script_video (id, org_id, ad_account_id, cluster, script, video_model, status, estimated_cost_usd)"
            " VALUES ($1,$2,$3,$4,$5,$6,'queued',$7)",
            vid,
            auth.org_id,
            acc,
            body.cluster,
            script,
            sv.VIDEO_MODEL,
            est["prep_usd"],
        )
    await queue.enqueue(
        db, auth.org_id, sv.JOB, {"script_video_id": str(vid)}, dedup_key=f"script_video:{vid}", max_attempts=1
    )
    return {"id": str(vid), "status": "queued"}


class ScriptBody(BaseModel):
    cluster: str


@router.post("/creator-script")
async def write_creator_script(
    body: ScriptBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """Write (or write again) the new-creator script for one kind of video: watching its two
    best videos (about a cent each, once per video) and one call for the script (under a cent).
    The page asks before calling it."""
    eid = await creator_script.experiment_id(db, auth.org_id)
    if eid is None:
        raise HTTPException(404, "no clusters for this account yet")
    if body.cluster == tc.OTHER:
        raise HTTPException(400, "Other is a mix of kinds; it gets no script")
    client = openai.AsyncOpenAI(
        api_key=settings.ai_gateway_api_key,
        base_url=f"{settings.ai_gateway_base_url}/v1",
        max_retries=0,
        timeout=300,
    )
    try:
        out = await creator_script.write(db, client, settings, blobs, auth.org_id, eid, body.cluster)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**out, "created_at": None}


async def _playbook(db: Database, eid, clusters: list[dict]) -> dict | None:
    """The findings organised by creative choice (scripts/write_playbook.py). Each choice
    carries its findings from every kind of video, and where to try it: videos that have the
    trait when the advice is to avoid it, videos that lack it when the advice is to do it."""
    row = await db.fetchrow(
        "SELECT payload, created_at FROM experiment_result WHERE experiment_id=$1 AND key='playbook'",
        eid,
    )
    if row is None:
        return None
    p = row["payload"]
    by_id = {f["id"]: (f, c) for c in clusters for f in c["findings"]}
    tier_rank = {"rule": 0, "test": 1, "context": 2, "story": 3}
    choices = []
    for key, ch in p["choices"].items():
        items = []
        for fid, pl in p["findings"].items():
            if pl["choice_key"] != key or fid not in by_id:
                continue
            f, c = by_id[fid]
            vids = c.get("read_videos") or {}
            codes = pl["videos_with"] if pl["direction"] == "avoid" else pl["videos_without"]
            targets = sorted(
                (dict(vids[x], code=x) for x in codes if x in vids),
                key=lambda v: -(v.get("spend") or 0),
            )[:3]
            items.append(
                {
                    **f,
                    "_codes": codes,
                    "cluster": c["name"],
                    "cluster_key": c["key"],
                    "cluster_spend": c["spend"],
                    "direction": pl["direction"],
                    "tier": pl["tier"],
                    "trait": pl["trait"],
                    "scripts": pl["scripts"],
                    "targets": targets,
                }
            )
        if not items:
            continue
        items.sort(key=lambda i: (tier_rank[i["tier"]], -i["cluster_spend"]))
        advice = [i for i in items if i["tier"] in ("rule", "test")]
        verdict = p["verdicts"].get(key, "none")
        section = (
            "mixed"
            if verdict == "mixed"
            else "rule"
            if any(i["tier"] == "rule" for i in advice)
            else "test"
            if advice
            else "story"
            if all(i["tier"] == "story" for i in items)
            else "context"
        )
        choices.append(
            {
                **ch,
                "key": key,
                "verdict": verdict,
                "section": section,
                "spend": sum(
                    {i["cluster_key"]: i["cluster_spend"] for i in advice or items}.values()
                ),
                "step": _step((advice or items)[0]),
                "headline": _headline((advice or items)[0]),
                "confidence": {
                    "tier": (advice or items)[0]["tier"],
                    "scripts": (advice or items)[0]["scripts"],
                    "p": _p((advice or items)[0]),
                },
                "stake": _stake(advice, clusters),
                "findings": items,
            }
        )
    for c in choices:
        for i in c["findings"]:
            i.pop("_codes", None)
    choices.sort(key=lambda c: -c["spend"])
    kinds = [c["name"] for c in clusters if c["key"] != "Other" and c["findings"]]
    grid = [
        {
            "key": c["key"],
            "name": c["name"],
            "section": c["section"],
            "cells": {
                i["cluster"]: {"direction": i["direction"], "tier": i["tier"]}
                for i in c["findings"]
                if i["tier"] in ("rule", "test")
            },
        }
        for c in choices
        if c["section"] in ("rule", "mixed", "test")
    ]
    return {
        "created_at": row["created_at"].isoformat(),
        "choices": choices,
        "kinds": kinds,
        "grid": grid,
    }


_STEPS = (
    ("buy", r"^(buy|convert|purchase|cpa|cost)"),
    ("click", r"^click"),
    ("stop", r"^(stop|hook|3s|watch)"),
)


def _comparisons(f: dict) -> list[tuple[str, dict]]:
    """Each "X vs Y" number with the funnel step its label names first ("Buy, of those who
    click" is a buying number)."""
    out = []
    for n in f.get("numbers") or []:
        if not re.search(r"\bvs\.?\b", n.get("value", "")):
            continue
        first = n.get("label", "").strip().lower()
        step = next((s for s, pat in _STEPS if re.search(pat, first)), None)
        out.append((step, n))
    return out


def _lead_number(f: dict) -> tuple[str | None, dict | None]:
    """The comparison to show without opening the row: the step closest to the sale, because
    a finding can help one step and hurt the next (urgency lifts clicks and lowers buying)."""
    comps = _comparisons(f)
    for step, _ in _STEPS:
        for s_, n in comps:
            if s_ == step:
                return step, n
    return comps[0] if comps else (None, None)


def _step(f: dict) -> str | None:
    return _lead_number(f)[0]


def _headline(f: dict) -> dict | None:
    n = _lead_number(f)[1]
    return {"label": n["label"], "value": n["value"]} if n else None


def _p(f: dict) -> float | None:
    for n in f.get("numbers") or []:
        m = re.search(r"\bp\s*[<=]?\s*([0-9.]+)", n.get("value", ""), re.I)
        if m:
            try:
                return float(m.group(1).rstrip("."))
            except ValueError:
                return None
    return None


def _stake(advice: list[dict], clusters: list[dict]) -> dict | None:
    """Last-30-day spend on the videos the advice would change: those with the trait when the
    advice is to avoid it, those without it when the advice is to add it."""
    reads = {c["key"]: c.get("read_videos") or {} for c in clusters}
    total, videos = 0.0, 0
    for f in advice:
        vids = reads.get(f["cluster_key"], {})
        for code in f.get("_codes") or []:
            v = vids.get(code)
            if v and v.get("spend"):
                total += v["spend"]
                videos += 1
    if not videos:
        return None
    return {"spend": round(total, 2), "videos": videos, "direction": advice[0]["direction"]}


async def _progress(db: Database, org_id) -> dict[str, list[dict]]:
    """What has been made around each finding: the briefs written to apply it, and for each,
    how many of its re-edits are rendered and how many openings were generated since."""
    rows = await db.fetch(
        """
        SELECT b.id, b.footage_id, b.status, b.focus->>'id' AS finding, b.created_at,
               (SELECT ad.name FROM footage f JOIN ad ON ad.creative_id = f.rep_creative_id
                 WHERE f.id = b.footage_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name,
               (SELECT count(DISTINCT r.code) FROM brief_render r WHERE r.brief_id = b.id AND r.status = 'done') AS renders,
               (SELECT count(*) FROM generation g WHERE g.footage_id = b.footage_id AND g.status = 'done'
                  AND g.created_at >= b.created_at) AS openings
        FROM creative_brief b
        WHERE b.org_id = $1 AND b.focus IS NOT NULL
        ORDER BY b.created_at DESC
        """,
        org_id,
    )
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["finding"], []).append(
            {
                "brief_id": str(r["id"]),
                "footage_id": str(r["footage_id"]),
                "name": r["name"],
                "status": r["status"],
                "renders": int(r["renders"]),
                "openings": int(r["openings"]),
                "created_at": r["created_at"].isoformat(),
            }
        )
    return out


def _status(progress: list[dict]) -> str:
    """The furthest any brief for this finding has got. Testing and answered come with Tests."""
    if any(p["renders"] or p["openings"] for p in progress):
        return "made"
    if any(p["status"] == "done" for p in progress):
        return "briefed"
    if any(p["status"] in ("queued", "running") for p in progress):
        return "writing"
    return "untested"


@router.get("/findings/{finding_id}")
async def finding(
    finding_id: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    """One finding by id, for the Make page that was opened from it."""
    f = (await findings_mod.all_findings(db, auth.org_id)).get(finding_id)
    if f is None:
        raise HTTPException(404, "finding not found")
    return f
