"""A video's ideas as one list, and the batch picked from them.

Re-edits, generated openings and lines to shoot are all variants of the
same video; the page used to show them as three products. Here each is an idea with one shape:
what it is, what it costs, how far along it is, what would stop it launching, and whether it is
in the batch. Only ideas that are finished video files can be launched; lines to shoot are
listed for the shoot, not for the batch. New-creator scripts belong to a cluster, not a video.
"""

from __future__ import annotations

import re
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id

MAX_IN_BATCH = 5
LAUNCH_HEIGHT = 720  # below this a render is a preview, not an ad
# What a test needs to read, per ad (see the brief's launch plan): openings are judged on the share
# still watching at 3s, the rest on cost per purchase.
HOOK_IMPRESSIONS = 5000
PURCHASES_PER_AD = 100


def _slug_title(name: str, code: str) -> str:
    tail = name.split("_")[-1] if "_" in name else ""
    words = re.sub(r"([a-z])([A-Z])", r"\1 \2", tail).strip()
    return words or code


def _sec(s: float) -> str:
    return f"{int(s // 60)}:{s % 60:04.1f}"


def ideas(brief: dict | None, generations: list[dict], decisions: dict[str, str]) -> list[dict]:
    out: list[dict] = []
    if brief and brief.get("status") == "done" and brief.get("content"):
        c = brief["content"]
        renders = brief.get("renders") or {}
        focus = (brief.get("focus") or {}).get("title")
        has_original = bool(brief.get("has_original"))
        placeholder_offer = not (c.get("offer") or "").strip() or "[" in (c.get("offer") or "")
        for batch in c.get("batches") or []:
            for v in batch.get("variants") or []:
                ref = f"brief:{brief['id']}:{v['code']}"
                edit = v.get("edit")
                idea = {
                    "ref": ref,
                    "kind": "reedit" if edit else "pickup",
                    "code": v["code"],
                    "title": _slug_title(v.get("name") or "", v["code"]),
                    "name": v.get("name"),
                    "change": v.get("change"),
                    "read_on": v.get("read_on"),
                    "finding": focus,
                    "cost_label": "Free re-edit" if edit else "Needs a shoot",
                    "cost_usd": 0 if edit else None,
                    "decision": decisions.get(ref),
                    "blockers": [],
                    "warnings": [],
                    "media_url": None,
                    "media_path": None,
                    "edit": edit,
                    "brief_id": brief["id"],
                }
                if not edit:
                    idea["state"] = "shoot"
                    out.append(idea)
                    continue
                r = renders.get(v["code"])
                if r is None:
                    idea["state"] = "idea"
                elif r["status"] in ("queued", "running"):
                    idea["state"] = "working"
                elif r["status"] == "failed":
                    idea["state"] = "failed"
                    idea["warnings"].append(
                        f"The last render failed: {r.get('error') or 'unknown error'}"
                    )
                else:
                    stale = r.get("edit") != edit or (has_original and not r.get("from_original"))
                    idea["state"] = "stale" if stale else "ready"
                    idea["media_url"] = r.get("url")
                    idea["media_path"] = r.get("path")
                    if (r.get("height") or 0) < LAUNCH_HEIGHT and not r.get("from_original"):
                        idea["blockers"].append(
                            f"Cut from a {r.get('width')}×{r.get('height')} copy. Upload the original file, then render again."
                        )
                if (
                    any(o.get("text") == "{offer}" for o in edit.get("overlays") or [])
                    and placeholder_offer
                ):
                    idea["blockers"].append(
                        "The end card offer is a placeholder. Set the current offer in the brief."
                    )
                for t in sorted(set(edit.get("tight") or [])):
                    idea["warnings"].append(
                        f"No clear pause at {_sec(t)}. Listen there before launching."
                    )
                out.append(idea)
        # New-creator scripts belong to the cluster, not to one video: not listed here.
    for g in generations:
        ref = f"gen:{g['id']}"
        state = {"queued": "working", "running": "working", "failed": "failed"}.get(
            g["status"], "ready"
        )
        idea = {
            "ref": ref,
            "kind": "opening",
            "code": "G",
            "title": "Generated opening",
            "name": None,
            "change": g.get("prompt"),
            "read_on": "Share still watching at 3s",
            "finding": None,
            "cost_label": f"Paid opening, about ${g['estimated_cost_usd']:.2f}"
            if g.get("estimated_cost_usd")
            else "Paid opening",
            "cost_usd": g.get("estimated_cost_usd"),
            "decision": decisions.get(ref),
            "state": state,
            "blockers": [],
            "warnings": [],
            "media_url": g.get("compare_url"),
            "result_url": g.get("result_url"),
            "media_path": g.get("result_path"),
            "edit": None,
            "generation_id": g["id"],
            "stage": g.get("stage"),
        }
        if state == "failed":
            idea["warnings"].append(f"Did not finish: {g.get('error')}")
        if state == "ready" and g.get("resolution") == "480p":
            idea["blockers"].append("A 480p look test. Generate it again at 720p to launch it.")
        out.append(idea)
    # Number the generated openings so they read as G1, G2 in order of creation.
    n = 0
    for i in reversed(out):
        if i["kind"] == "opening":
            n += 1
            i["code"] = f"G{n}"
    return out


def batch_view(items: list[dict], batch: dict | None, cpa: float | None) -> dict:
    chosen = [i for i in items if i["decision"] == "in"]
    ready = [i for i in chosen if i["state"] == "ready" and not i["blockers"]]
    arms = len(chosen) + 1  # the original runs as the control
    return {
        "id": batch["id"] if batch else None,
        "status": batch["status"] if batch else "drafting",
        "experiment_id": batch.get("experiment_id") if batch else None,
        "chosen": [i["ref"] for i in chosen],
        "ready": len(ready),
        "max": MAX_IN_BATCH,
        "can_launch": bool(chosen) and len(ready) == len(chosen),
        "needs": {
            "arms": arms,
            "hook_impressions": HOOK_IMPRESSIONS * arms,
            "purchases": PURCHASES_PER_AD * arms,
            "budget_for_purchases": round(PURCHASES_PER_AD * arms * cpa) if cpa else None,
        },
    }


async def drafting_batch(
    db: Database, org_id: UUID, ad_account_id: UUID, footage_id: UUID, create: bool = False
) -> dict | None:
    row = await db.fetchrow(
        "SELECT * FROM make_batch WHERE footage_id=$1 AND org_id=$2 AND status='drafting'",
        footage_id,
        org_id,
    )
    if row is None and create:
        bid = new_id()
        await db.execute(
            "INSERT INTO make_batch (id, org_id, ad_account_id, footage_id, status) VALUES ($1,$2,$3,$4,'drafting')",
            bid,
            org_id,
            ad_account_id,
            footage_id,
        )
        row = await db.fetchrow("SELECT * FROM make_batch WHERE id=$1", bid)
    return (
        {
            **dict(row),
            "id": str(row["id"]),
            "experiment_id": str(row["experiment_id"]) if row["experiment_id"] else None,
        }
        if row
        else None
    )


async def decisions(db: Database, batch_id: str | None) -> dict[str, str]:
    if not batch_id:
        return {}
    return {
        r["ref"]: r["decision"]
        for r in await db.fetch(
            "SELECT ref, decision FROM make_batch_item WHERE batch_id=$1", UUID(batch_id)
        )
    }


# ---------------------------------------------------------------------------------------------
# Where each idea changes the video, for the timeline and the before/after.

OPENING_S = 5.0
CLOSE_FRACTION = 0.75
STOPWORDS = {
    "the", "and", "that", "this", "with", "from", "your", "have", "they", "their", "about", "into", "when", "what",
    "says", "said", "video", "videos", "ad", "ads", "or", "a", "an", "of", "to", "in", "on", "it", "is", "are", "be",
    "for", "as", "by", "at", "its", "it's", "any", "more", "than", "one", "not", "no", "does", "do", "like", "mention",
    "mentions", "uses", "use", "shows", "show", "script", "scripts", "line", "lines", "whether", "there", "which",
    "before", "after", "while", "time", "times", "just", "some", "such", "call", "action", "then", "only",
}


def part_at(t: float, duration: float) -> str:
    if t < min(OPENING_S, duration * 0.2):
        return "opening"
    if t >= duration * CLOSE_FRACTION:
        return "close"
    return "middle"


def changes(idea: dict, duration: float) -> dict | None:
    """The source range an idea changes, the part of the video that is, and where the change
    shows in the new version (for the before/after)."""
    kind, edit = idea["kind"], idea.get("edit")
    if kind in ("pickup", "opening"):
        return {"start": 0.0, "end": min(OPENING_S, duration), "part": "opening", "output_at": 0.0, "how": "replaces"}
    if kind == "script" or not edit:
        return None
    segs = [(float(a), float(b)) for a, b in edit.get("segments") or []]
    overlays = edit.get("overlays") or []
    if segs and segs[0][0] > 0.5:  # a later line moved to the front
        a, b = segs[0]
        return {"start": a, "end": b, "part": "opening", "output_at": 0.0, "how": "moves to the start"}
    kept = sorted(segs)
    gaps, cursor = [], 0.0
    for a, b in kept:
        if a - cursor > 0.3:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if duration - cursor > 0.3 and not overlays:
        gaps.append((cursor, duration))
    if gaps:
        a, b = gaps[0]
        out_at = sum(max(0.0, min(e, a) - s) for s, e in segs)
        return {"start": a, "end": b, "part": part_at((a + b) / 2, duration), "output_at": round(out_at, 2), "how": "cuts"}
    if overlays:
        o = overlays[0]
        s, e = float(o.get("start", 0)), min(float(o.get("end", duration)), duration)
        return {"start": s, "end": e, "part": "close" if o.get("text") == "{offer}" else part_at(s, duration), "output_at": s, "how": "adds on screen"}
    return None


def _stems(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z']+", text.lower()):
        w = w.strip("'")
        if len(w) < 4 or w in STOPWORDS:
            continue
        if w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w[:5])
    return out


def locate(trait: str, sentences: list[dict]) -> list[dict]:
    """The sentences of this video that most likely carry a finding's trait. Words from the
    trait's quoted example count triple; words that appear in many of the video's sentences
    count less (they say little about any one line). A guess to check by eye, labelled so."""
    import math

    if not sentences or re.search(r"end screen|end card|on-screen|on screen|caption|overlay", trait, re.I):
        # A trait about text on screen cannot be found in what is said.
        return []
    quoted = set(_stems(" ".join(re.findall(r"['\u2018\u201c\"]([^'\u2019\u201d\"]{4,})['\u2019\u201d\"]", trait))))
    words = set(_stems(trait))
    toks = [set(_stems(s["text"])) for s in sentences]
    n = len(sentences)
    df = {w: sum(1 for t in toks if w in t) for w in words}
    # A word this video says in many lines (the product, "magnesium") cannot point to one line.
    common = {w for w, d in df.items() if d > max(1, n * 0.12)}
    words -= common
    scored = []
    for s, t in zip(sentences, toks, strict=True):
        score = sum((3.0 if w in quoted else 1.0) * math.log(1 + n / df[w]) for w in words & t if df[w])
        if score:
            scored.append((score, len(words & t), s))
    if not scored:
        return []
    def strong(t: set[str], k: int) -> bool:
        # Two trait words in one line, or one distinctive word from the trait's own example.
        return k >= 2 or any(w in quoted and df.get(w) == 1 for w in words & t)

    scored = [(sc, k, s) for sc, k, s in scored if strong(toks[sentences.index(s)], k)]
    if not scored:
        return []
    best = max(sc for sc, _, _ in scored)
    return [s for sc, _, s in scored if sc >= best * 0.8]
