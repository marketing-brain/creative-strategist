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

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.api.routers import experiments as ex
from cs.briefs import findings as findings_mod
from cs.core.blobs import BlobStore
from cs.core.db import Database

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
    for c in out:
        for f in c["findings"]:
            f["progress"] = progress.get(f["id"], [])
            f["status"] = _status(f["progress"])
    body["playbook"] = await _playbook(db, eid, out)
    return body


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
