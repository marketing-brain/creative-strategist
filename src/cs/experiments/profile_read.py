"""Experiment 5: what separates a cluster's best videos from its worst, from blind profiles.

Every video in the cluster has a blind creative profile (scripts/profile_video.py, two runs,
fixed values kept only when the runs agree). Code turns each into one row of trusted features,
using only the fields that scored well against the hand-labelled answer key (2026-09-25) and
replacing the ones that did not with measurements:

- kept from the model: hook type, awareness level, proof types, production, main speaker,
  hook text on screen (a value only when both runs agree; otherwise unknown);
- measured in code: pace (ffmpeg scene cuts), running captions (the tagger's on-screen text
  between the opening and the end card; none without speech), sound-off (captions, or no
  speech), when the product first appears (scene table), length;
- dropped: music and music changes (the model reported music on a file with no audio track),
  product interaction.

Code then counts, for every feature value, how many winners and losers have it (a twin family
counts once, and a family on both sides counts for neither) and the pooled cost per purchase of
the videos with it and without it. The model writes the read from those counts, the profiles and
the numbers; it never counts. Every claim it makes is checked against the counts afterwards.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database
from cs.experiments import cluster_why as cw

PURPOSE = "experiments.profile_read"
VERSION = "profile-read-v1"
SCENE_THRESHOLD = 0.15  # checked against hand-counted cuts on the answer-key videos
MEASURE_VERSION = 2  # 2: "(no speech)" transcripts count as no speech

# Facts verified by hand on 2026-09-25, passed to the read so it does not explain around them.
KNOWN_ISSUES = {
    "01a0ca3a-790c-7078-9226-a6aeeae7c6cf": "The creator holds another brand's product (a 'CleanSleep' capsule bottle) throughout; Root Labs appears only on the end card.",
    "01a0ca3a-790f-7007-bde5-5976038dcbe9": "No speech and no audio track: text over a man acting out symptoms. It is in this talking-head cluster by mistake.",
}

PROOF = ["testimonial", "authority", "science_or_mechanism", "reviews_or_ratings", "demonstration",
         "before_after", "comparison"]


def count_cuts(video: bytes) -> int:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "v.mp4")
        p.write_bytes(video)
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(p), "-vf", f"select='gt(scene,{SCENE_THRESHOLD})',showinfo",
             "-an", "-f", "null", "-"],
            capture_output=True, text=True,
        ).stderr
        return len(re.findall(r"pts_time:", out))


async def measure(db: Database, blobs, experiment_id: UUID, v: cw.Video) -> dict:
    """Code-measured facts for one video, cached as experiment_result `measure:<footage_id>`."""
    key = f"measure:{v.id}"
    got = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key=$2", experiment_id, key)
    if got and got.get("threshold") == SCENE_THRESHOLD and got.get("v") == MEASURE_VERSION:
        return got
    path = await db.fetchval(
        "SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id WHERE c.id=$1", v.rep_creative_id)
    cuts = count_cuts(await blobs.get(path))
    run = await db.fetchval(
        "SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1 AND source_tag_run_id IS NOT NULL LIMIT 1", v.id)
    dur = float(v.duration_s or 0)
    body_text = await db.fetchval(
        "SELECT count(*) FROM tag WHERE tag_run_id=$1 AND dimension='overlay_texts' AND t_start_s >= 3 AND t_start_s < $2",
        run, dur - 6) if run else 0
    first_product = await db.fetchval("SELECT min(t_start_s) FROM scene WHERE footage_id=$1 AND shows_product", v.id)
    out = {"threshold": SCENE_THRESHOLD, "cuts": cuts, "duration_s": dur, "speech": _has_speech(v.transcript), "v": MEASURE_VERSION,
           "body_text_items": int(body_text or 0), "first_product_s": float(first_product) if first_product is not None else None}
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()", experiment_id, key, out)
    return out


def _has_speech(transcript: str | None) -> bool:
    t = (transcript or "").strip().lower()
    return bool(t) and t not in ("(no speech)", "no speech", "none")


def _bucket(x: float | None, edges: list[tuple[float, str]], last: str) -> str | None:
    if x is None:
        return None
    for edge, name in edges:
        if x < edge:
            return name
    return last


# Content fields from the v3 profile (`profile3:`) that scored well against the answer key on
# 2026-09-25 (8-9 of 10 right, at most 1 wrong, or list fields with few extras). Promise
# specificity, speed claim, ending, outcomes and call-to-action directness scored poorly and are
# left out.
V3_SCALAR = ["price_stated", "urgency", "controversy", "attacks_other_magnesium", "mechanism_explained",
             "narrowness", "tone", "addressed_to"]
V3_LISTS = {
    "offer_types": ["discount", "price_drop", "bundle", "free_gift_or_shipping", "guarantee"],
    "vouched_by": ["own_experience", "professional_recommendation", "stated_credential", "public_figure_clip",
                   "customer_reviews", "lab_or_third_party_test", "studies_or_science"],
    "objections_answered": ["price", "taste_or_format", "tried_magnesium_before", "side_effects_or_safety",
                            "grogginess_or_dependency", "is_it_legit"],
}


def features(profile: dict | None, m: dict, profile3: dict | None = None) -> dict[str, Any]:
    """One row of trusted features. None = unknown (the model's runs disagreed, or no profile).
    Hook type, awareness, proof types, speaker, production and hook text come from the v2
    profile where there is one, else from v3, which asks the same fields the same way."""
    fx = (profile or profile3 or {}).get("fixed", {})

    def agreed(k):
        v = fx.get(k)
        return v["value"] if v and v.get("agreed") else None

    speaker = "text_only" if not m["speech"] else agreed("main_speaker")
    captions = False if not m["speech"] else m["body_text_items"] >= m["duration_s"] / 6
    row: dict[str, Any] = {
        "hook_type": agreed("hook_type"),
        "awareness_level": agreed("awareness_level"),
        "production": agreed("production"),
        "main_speaker": speaker,
        "hook_text_on_screen": agreed("hook_text_on_screen"),
        "running_captions": captions,
        "sound_off_understandable": captions or speaker == "text_only",
        "pace": _bucket(m["cuts"] / m["duration_s"] * 10 if m["duration_s"] else None, [(1, "slow"), (3, "medium")], "fast"),
        "product_first_shown": _bucket(m["first_product_s"], [(20, "by 0:20"), (40, "0:20-0:40")], "after 0:40"),
        "length": _bucket(m["duration_s"], [(60, "under 60s"), (100, "60-100s")], "over 100s"),
    }
    proof = fx.get("proof_types")
    for p in PROOF:
        if not proof or p in proof.get("soft", []):
            row[f"proof:{p}"] = None
        else:
            row[f"proof:{p}"] = p in proof["value"]
    f3 = (profile3 or {}).get("fixed", {})
    for k in V3_SCALAR:
        v = f3.get(k)
        row[k] = v["value"] if v and v.get("agreed") else None
    for k, values in V3_LISTS.items():
        v = f3.get(k)
        for val in values:
            if not v or val in v.get("soft", []):
                row[f"{k}:{val}"] = None
            else:
                row[f"{k}:{val}"] = val in v["value"]
        if v:
            row[f"{k}:any"] = bool(set(v["value"]) - {"none"}) if v.get("agreed") or set(v["value"]) - {"none"} else None
    return row


def counts(videos: list[cw.Video], rows: dict[str, dict], twin_groups: list[list[cw.Video]]) -> list[dict]:
    """For every feature value, over every ranked video: how many videos and distinct scripts
    have it, the median and pooled cost per purchase with and without it, and, for reference,
    which winner and loser scripts have it (a script with edits on both sides counts for
    neither; in Talking head · Late reveal that leaves 1 winner script and 2 loser scripts, so
    the extremes alone cannot carry a comparison)."""
    fam = {v.code: v.code for v in videos}
    for g in twin_groups:
        for v in g:
            fam[v.code] = g[0].code
    ranked = [v for v in videos if v.side in ("best", "middle", "worst")]
    sides: dict[str, set[str]] = defaultdict(set)
    for v in ranked:
        sides[fam[v.code]].add(v.side)
    best_f = {f for f, s in sides.items() if "best" in s and "worst" not in s}
    worst_f = {f for f, s in sides.items() if "worst" in s and "best" not in s}

    def cpa(vs):
        sp, pu = sum(v.sums["spend"] for v in vs), sum(v.sums["conversions"] for v in vs)
        return round(sp / pu, 2) if pu else None

    def median_cpa(vs):
        xs = sorted(v.sums["spend"] / v.sums["conversions"] for v in vs if v.sums["conversions"])
        if not xs:
            return None
        m = len(xs) // 2
        return round(xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2, 2)

    out = []
    for k in sorted({k for r in rows.values() for k in r}):
        for val in sorted({str(r[k]) for r in rows.values() if r.get(k) is not None}):
            has = [v for v in ranked if rows[v.code].get(k) is not None and str(rows[v.code][k]) == val]
            lacks = [v for v in ranked if rows[v.code].get(k) is not None and str(rows[v.code][k]) != val]
            if not has or not lacks:
                continue
            hf = {fam[v.code] for v in has}
            out.append({
                "feature": k, "value": val,
                "winners_with": sorted(hf & best_f), "winners_n": len(best_f),
                "losers_with": sorted(hf & worst_f), "losers_n": len(worst_f),
                "videos_with": [v.code for v in has], "videos_without": [v.code for v in lacks],
                "unknown": [v.code for v in ranked if rows[v.code].get(k) is None],
                "cpa_with": cpa(has), "cpa_without": cpa(lacks),
                "median_cpa_with": median_cpa(has), "median_cpa_without": median_cpa(lacks),
                "scripts_with": len({fam[v.code] for v in has}), "scripts_without": len({fam[v.code] for v in lacks}),
            })
    return out


SYSTEM = """You are a senior performance-creative strategist. You are reading one cluster of similar video ads from one brand, for the team that made them: what makes the best performers the best, and the worst the worst? Do not recommend changes, tests, or actions.

You get:
- every video's numbers over the last 30 days (precomputed): cost per purchase, spend, where it ran, and its funnel steps against the rest of the cluster;
- a blind creative profile of every video, written without its numbers (concept, intended persuasion, key moments);
- a feature table built by code from the profiles and from measurements;
- COUNTS: for every feature value, which winners and losers have it (edits of one script count once) and the pooled cost per purchase of videos with and without it;
- the noise floor: how far cost per purchase moved between edits of the same script;
- known issues verified by hand.

READ THE PERFORMANCE
- All numbers and counts are precomputed. Do not calculate, re-round, or invent them. Reproduce numbers exactly as supplied.
- Establish the overall pattern before explaining individual videos. The weakest metric is not automatically the main issue, and strong attention does not automatically mean strong sales.
- CPM is set by the auction and where the ad ran, not by the video. Scaling campaigns reach warmer audiences than testing ad sets.

RECONCILE THE TWO
- Which differences between winners and losers do the COUNTS support? Which fit what the creatives are designed to do, and which challenge that?
- For each main finding, connect: specific creative evidence (video and moment) -> plausible viewer response -> measured behaviour.
- A finding must rest on the COUNTS: say how many videos and scripts have it and the median cost per purchase with and without it. "Favours winners" means videos with it cost less per purchase. If winners and losers both have it, it is not the reason. Scripts whose edits land on both sides say more about delivery and chance than about content.
- A gap smaller than the noise floor between two single videos may be chance; say so rather than explaining it.
- Consider the whole ad; the relationship between the opening promise and later proof may matter more than one line.
- If no defensible creative explanation exists, say so. Say what remains unexplained.
- Do not treat frame shape or aspect ratio as a factor.

LANGUAGE
Keep observations, performance findings, and hypotheses distinct. Use "one plausible explanation is" or "is consistent with". Do not present causation or viewer reactions as established facts. Do not blame targeting, budget, placement, the algorithm, or seasonality without evidence; acknowledge outside influences briefly when they materially limit the reading. Plain words.

OUTPUT (JSON)
- read: two short paragraphs, 150-250 words in total. Start with the overall pattern and its key supplied numbers, then the strongest supported creative connections, then what is uncertain or unexplained.
- findings: each with the feature and value it rests on (exactly as written in COUNTS), whether it favours winners or losers, the evidence (video code, MM:SS, what happens), and a strength: "supported" (clearly separates winners from losers in COUNTS), "lead" (leans that way on few videos), or "unclear".
- unexplained: videos or results the read cannot explain, each with a short reason.
- caveats: short list."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "read": {"type": "string"},
        "findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "claim": {"type": "string"},
                "feature": {"type": "string"},
                "value": {"type": "string"},
                "favours": {"type": "string", "enum": ["winners", "losers"]},
                "strength": {"type": "string", "enum": ["supported", "lead", "unclear"]},
                "evidence": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"video": {"type": "string"}, "at": {"type": "string"}, "what": {"type": "string"}},
                    "required": ["video", "at", "what"]}},
            },
            "required": ["claim", "feature", "value", "favours", "strength", "evidence"]}},
        "unexplained": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"video": {"type": "string"}, "why": {"type": "string"}},
            "required": ["video", "why"]}},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["read", "findings", "unexplained", "caveats"],
}


def brief(b: cw.Brief, profiles: dict[str, dict], rows: dict[str, dict], table: list[dict]) -> str:
    rankable = sorted([v for v in b.videos if v.side != "unrankable"], key=lambda v: -(v.shrunk or 0))
    rank_of = {v.id: i for i, v in enumerate(rankable, 1)}
    spend = sum(v.sums["spend"] for v in b.videos)
    conv = sum(v.sums["conversions"] for v in b.videos)
    parts = [
        f"CLUSTER: {b.cluster}. WINDOW: the account's last 30 days, {b.span[0]} to {b.span[1]}.",
        f"{len(b.videos)} videos; {len(rankable)} have enough clicks to rank. Winners = best third by cost per purchase "
        f"({sum(v.side == 'best' for v in b.videos)} videos), losers = worst third ({sum(v.side == 'worst' for v in b.videos)}). "
        f"Cluster cost per purchase ${spend / conv:,.0f}.",
    ]
    noise = []
    for g in b.twin_groups:
        cpas = [f"{v.code} ${v.sums['spend'] / v.sums['conversions']:,.0f}" for v in g if v.sums["conversions"]]
        if len(cpas) > 1:
            noise.append(" vs ".join(cpas))
    parts.append("NOISE FLOOR (edits of the same script): " + ("; ".join(noise) if noise else "no twins"))
    issues = [f"{v.code}: {KNOWN_ISSUES[str(v.id)]}" for v in b.videos if str(v.id) in KNOWN_ISSUES]
    if issues:
        parts.append("KNOWN ISSUES (verified by hand):\n" + "\n".join(issues))
    lines = ["COUNTS over every ranked video (feature=value: videos and distinct scripts with / without it; "
             "median cost per purchase with vs without; pooled cost per purchase with vs without; then, for reference, "
             "winner and loser scripts with it). Note: scripts with edits among both winners and losers count for neither side, "
             "so the winner/loser columns rest on very few scripts; lean on the medians across all ranked videos."]
    for t in table:
        lines.append(
            f"  {t['feature']}={t['value']}: {len(t['videos_with'])} videos / {t['scripts_with']} scripts with {t['videos_with']}, "
            f"{len(t['videos_without'])} / {t['scripts_without']} without; median CPA ${t['median_cpa_with']} vs ${t['median_cpa_without']}; "
            f"pooled ${t['cpa_with']} vs ${t['cpa_without']}; winners {len(t['winners_with'])}/{t['winners_n']}, losers {len(t['losers_with'])}/{t['losers_n']}"
            + (f"; unknown for {t['unknown']}" if t["unknown"] else ""))
    parts.append("\n".join(lines))
    for v in b.videos:
        s = v.sums
        head = {"best": "WINNER", "worst": "LOSER", "middle": "MIDDLE", "unrankable": "NOT RANKED (too few clicks)"}[v.side]
        cpa = f"${s['spend'] / s['conversions']:,.0f}" if s["conversions"] else "no purchases"
        steps = "; ".join(
            (f"cpm ${st['mine']:,.0f} vs ${st['theirs']:,.0f}" if st["key"] == "cpm" else f"{st['key']} {st['mine']:.1%} vs {st['theirs']:.1%}")
            + (" CLEAR" if st["clear"] else "")
            for st in (v.funnel or {}).get("steps", []) if st["mine"] is not None and st["theirs"] is not None)
        spend_v = sum(v.where.values()) or 1
        where = ", ".join(f"{k} {x / spend_v:.0%}" for k, x in sorted(v.where.items(), key=lambda kv: -kv[1]))
        p = profiles.get(v.code) or {}
        cp = p.get("creative_profile", {})
        moments = "; ".join(f"{m['at']} {m['observed']}" for m in p.get("moments", [])[:10])
        parts.append("\n".join([
            f"=== {v.code} · {head}" + (f" (rank {rank_of[v.id]} of {len(rankable)})" if v.id in rank_of else ""),
            f"numbers: spend ${s['spend']:,.0f}, purchases {s['conversions']:.0f}, cost per purchase {cpa}; where it ran: {where}",
            f"funnel vs rest of cluster: {steps}",
            f"features: {json.dumps(rows[v.code])}",
            f"concept: {cp.get('concept_and_message', {}).get('description', 'no profile')}",
            f"intended persuasion: {cp.get('intended_persuasion', '')}",
            f"moments: {moments}",
        ]))
    parts.append("Based on the data above: what makes the winners the winners and the losers the losers in this cluster?")
    return "\n\n".join(parts)


def check(out: dict, table: list[dict], codes: set[str]) -> list[dict]:
    """Attach the true counts to each finding and flag what the counts contradict."""
    by = {(t["feature"], t["value"]): t for t in table}
    checked = []
    for f in out.get("findings", []):
        t = by.get((f["feature"], f["value"]))
        flags = []
        if not t:
            flags.append("feature/value not in the counts")
        else:
            mw, mo = t["median_cpa_with"], t["median_cpa_without"]
            if mw is not None and mo is not None:
                # "favours winners" means videos with it cost less per purchase.
                if (f["favours"] == "winners" and mw >= mo) or (f["favours"] == "losers" and mw <= mo):
                    flags.append(f"medians contradict it (with ${mw} vs without ${mo})")
            if t["scripts_with"] < 3 or t["scripts_without"] < 3:
                flags.append(f"rests on few scripts ({t['scripts_with']} with, {t['scripts_without']} without)")
        bad = [e["video"] for e in f["evidence"] if e["video"] not in codes]
        if bad:
            flags.append(f"unknown videos {bad}")
        checked.append({**f, "counts": t, "flags": flags})
    return checked


async def explain(db: Database, client, model: str, org_id: UUID, experiment_id: UUID, cluster: str, text: str) -> tuple[dict, dict]:
    claim = await model_calls.claim(db, org_id, PURPOSE, f"{experiment_id}:{cluster}:{VERSION}", model)
    try:
        resp = await client.chat.completions.create(
            model=model, max_tokens=24000,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            response_format={"type": "json_schema", "json_schema": {"name": "profile_read", "schema": SCHEMA, "strict": True}},
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = resp.usage
    usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}
    await model_calls.finish(db, claim.id, **usage)
    return out, usage
