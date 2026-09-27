"""Experiment 5: why the best videos in a cluster beat the worst. One model call per cluster.

Code measures, the model explains, code checks.

Measured here, with no model: the cluster's members as the clusters page lists them ($200+
lifetime spend, delivery in the account's last 30 days), each video's funnel steps against the
rest of the cluster over those 30 days (cs.experiments.why), where it ran (testing, Advantage+
scaling, retargeting), and near-twin videos (the same script, found by transcript text).

The model gets the best and worst thirds of the rankable videos with their numbers and their
evidence, and says for each one which step it won or lost at and what in the video sits there,
quoting the evidence. It also names what recurs among the winners and not the losers, and what
the twins show.

Checked afterwards: a cited step must be one code marked clear for that video, a quote must be
in that video's evidence, a pattern must hold on at least two winners and more often among
winners than losers (counted here, not taken from the model), and the model's own sentences may
not carry numbers. Anything that fails is dropped and listed, never repaired.

What stays out, and why (graded 2026-09-25): the `captions` tag (tagger agreement 0.59), ad and
ad set names, and experiment 1's model-written descriptions. The watch curve is not used yet
(play_curve_point covers these videos; it needs a per-second event log to pair with). Visual claims are limited to face, product and packaging on screen.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database
from cs.core.repo import rollups
from cs.core.repo.creatives import _SUMS_SQL
from cs.experiments import tag_clusters as tc
from cs.experiments import why
from cs.experiments.describe import video_evidence

PURPOSE = "experiments.cluster_why"
VERSION = "cluster-why-v2"
MIN_LIFETIME_SPEND = 200.0
MIN_CLICKS = 100  # below this a video's purchase rate is too thin to rank
PRIOR_PURCHASES = 3.0  # a ranked rate is pulled toward the cluster's by this many purchases
TWIN_RATIO = 0.7  # transcript similarity at which two files are the same script (measured:
# re-edits of one script 0.74-0.98, different scripts far lower)
TWIN_MIN_CHARS = 200


def _f(x: Any) -> float:
    return float(x or 0)


@dataclass
class Video:
    id: UUID
    duration_s: float | None
    rep_creative_id: UUID | None
    name: str | None
    sums: dict[str, float]
    where: dict[str, float] = field(default_factory=dict)  # spend by campaign kind
    spanish_spend: float = 0.0
    first_day: date | None = None
    last_day: date | None = None
    evidence: str = ""
    transcript: str = ""
    code: str = ""  # V01.. in the brief
    side: str = "middle"  # best / worst / middle / unrankable
    shrunk: float | None = None
    funnel: dict | None = None


async def cluster_members(db: Database, org_id: UUID, experiment_id: UUID) -> tuple[tuple[date, date], dict[str, list[Video]]]:
    """The clusters page's list: $200+ lifetime spend, delivery in the last 30 days."""
    span = await rollups.rollup_window(db, org_id)
    assert span, "no delivery"
    rows = await db.fetch(
        """
        SELECT DISTINCT ON (f.id) f.id, f.duration_s, f.rep_creative_id,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                 ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name,
               COALESCE(life.spend, 0) AS lifetime_spend
        FROM experiment_item i JOIN footage f ON f.id = i.footage_id
        LEFT JOIN footage_metric_window life ON life.footage_id = f.id AND life.window_kind = 'lifetime'
        WHERE i.experiment_id = $1
        """,
        experiment_id,
    )
    rows = [r for r in rows if _f(r["lifetime_spend"]) >= MIN_LIFETIME_SPEND]
    ids = [r["id"] for r in rows]
    sums = {
        r["footage_id"]: {k: _f(v) for k, v in dict(r).items() if k != "footage_id"}
        for r in await db.fetch(
            f"""
            SELECT fc.footage_id, {_SUMS_SQL}
            FROM metric_daily m JOIN footage_creative fc ON fc.creative_id = m.creative_id
            WHERE m.org_id = $1 AND fc.footage_id = ANY($2::uuid[]) AND m.day BETWEEN $3 AND $4
            GROUP BY fc.footage_id
            """,
            org_id, ids, span[0], span[1],
        )
    }
    labels: dict[str, dict[UUID, str]] = defaultdict(dict)
    for r in await db.fetch(
        "SELECT footage_id, axis, value FROM experiment_label WHERE experiment_id=$1", experiment_id
    ):
        labels[r["axis"]][r["footage_id"]] = r["value"]
    cmap = tc.cluster_map(
        {f: {ax: labels[ax][f] for ax in ("who", "animated", "reveal") if f in labels[ax]} for f in labels["who"]}
    )
    out: dict[str, list[Video]] = defaultdict(list)
    for r in rows:
        s = sums.get(r["id"])
        c = cmap.get(r["id"])
        if not s or not s["impressions"] or c is None:
            continue
        out[c].append(Video(r["id"], r["duration_s"], r["rep_creative_id"], r["name"], s))
    return span, out


def _kind_sql() -> str:
    return (
        "CASE WHEN a.adset_name LIKE 'TESTING%' THEN 'testing'"
        " WHEN a.campaign_name ~* 'retarget|remarket' THEN 'retargeting' ELSE 'scaling' END"
    )


async def add_context(db: Database, org_id: UUID, span: tuple[date, date], vs: list[Video]) -> None:
    """Where each video ran in the window, when it first ran at all, and its evidence."""
    by = {v.id: v for v in vs}
    for r in await db.fetch(
        f"""
        SELECT fc.footage_id, {_kind_sql()} AS kind, sum(m.spend) AS spend,
               sum(m.spend) FILTER (WHERE a.campaign_name ILIKE '%spanish%') AS spanish
        FROM metric_daily m JOIN ad a ON a.id = m.ad_id
        JOIN footage_creative fc ON fc.creative_id = a.creative_id
        WHERE m.org_id = $1 AND fc.footage_id = ANY($2::uuid[]) AND m.day BETWEEN $3 AND $4
        GROUP BY 1, 2
        """,
        org_id, list(by), span[0], span[1],
    ):
        v = by[r["footage_id"]]
        v.where[r["kind"]] = _f(r["spend"])
        v.spanish_spend += _f(r["spanish"])
    for r in await db.fetch(
        """
        SELECT fc.footage_id, min(m.day) AS first, max(m.day) AS last
        FROM metric_daily m JOIN footage_creative fc ON fc.creative_id = m.creative_id
        WHERE m.org_id = $1 AND fc.footage_id = ANY($2::uuid[]) AND m.impressions > 0
        GROUP BY 1
        """,
        org_id, list(by),
    ):
        by[r["footage_id"]].first_day, by[r["footage_id"]].last_day = r["first"], r["last"]
    for v in vs:
        ev = await video_evidence(db, {"id": v.id, "duration_s": v.duration_s, "rep_creative_id": v.rep_creative_id})
        v.evidence = _clean_facts(ev.text)
        m = re.search(r"^Transcript: (.*?)(?:\nAd copy shown with it:|\Z)", ev.text, re.S | re.M)
        v.transcript = m.group(1).strip() if m else ""


def _clean_facts(text: str) -> str:
    """Drop the captions tag (agreement 0.59) and mark messenger as context only."""
    lines = []
    for line in text.splitlines():
        if line.startswith("Facts: "):
            parts = [p for p in line[len("Facts: "):].split("; ") if not p.startswith("captions = ")]
            parts = [p + " (context only; wrong for reaction videos)" if p.startswith("messenger = ") else p for p in parts]
            line = "Facts: " + "; ".join(parts)
        lines.append(line)
    return "\n".join(lines)


def rank(vs: list[Video]) -> None:
    """Best and worst thirds of the rankable videos by purchases per dollar (cost per purchase,
    what the brand pays), each pulled toward the cluster's rate by PRIOR_PURCHASES. Ranking on
    purchases per impression was tried first and rewarded expensive audiences: a $136 CPM video
    ranked best at $81 a purchase. How much of a video's standing came from cheap delivery
    rather than content is the funnel's cpm step."""
    spend = sum(v.sums["spend"] for v in vs)
    conv = sum(v.sums["conversions"] for v in vs)
    m = conv / spend if spend else 0
    rankable = [v for v in vs if v.sums["link_clicks"] >= MIN_CLICKS and m > 0]
    for v in vs:
        v.side = "unrankable"
        if m > 0:
            v.shrunk = (v.sums["conversions"] + PRIOR_PURCHASES) / (v.sums["spend"] + PRIOR_PURCHASES / m)
    rankable.sort(key=lambda v: -(v.shrunk or 0))
    third = max(1, len(rankable) // 3)
    for i, v in enumerate(rankable):
        v.side = "best" if i < third else "worst" if i >= len(rankable) - third else "middle"
    total = {k: sum(v.sums[k] for v in vs) for k in vs[0].sums}
    for v in vs:
        rest = {k: total[k] - v.sums[k] for k in total}
        v.funnel = why.decompose(v.sums, rest, "video", noun="cluster")
    # With tens of thousands of impressions almost every step clears its own 95% range, so a
    # step is only "clear" when it is also unusual for this cluster: outside the middle half of
    # the rankable videos' values for that step.
    spread: dict[str, tuple[float, float]] = {}
    for key in ("cpm", "stop", "click", "buy"):
        vals = sorted(st["mine"] for v in rankable for st in (v.funnel or {}).get("steps", [])
                      if st["key"] == key and st["mine"] is not None)
        if len(vals) >= 4:
            spread[key] = (_quantile(vals, 0.25), _quantile(vals, 0.75))
    for v in vs:
        for st in (v.funnel or {}).get("steps", []):
            lo_hi = spread.get(st["key"])
            st["unusual"] = bool(lo_hi and st["mine"] is not None and not lo_hi[0] <= st["mine"] <= lo_hi[1])
            st["clear"] = bool(st["clear"] and st["unusual"])


def _quantile(xs: list[float], q: float) -> float:
    i = (len(xs) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúñü]+", " ", s.lower()).strip()


def twins(vs: list[Video]) -> list[list[Video]]:
    """Files carrying the same script, by transcript similarity."""
    parent = {v.id: v.id for v in vs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ok = [v for v in vs if len(v.transcript) >= TWIN_MIN_CHARS]
    for i, a in enumerate(ok):
        for b in ok[i + 1:]:
            # autojunk off: on long texts it discards common characters and scored twins 0.01.
            if SequenceMatcher(None, _norm(a.transcript), _norm(b.transcript), autojunk=False).ratio() >= TWIN_RATIO:
                parent[find(a.id)] = find(b.id)
    groups: dict[UUID, list[Video]] = defaultdict(list)
    for v in vs:
        groups[find(v.id)].append(v)
    return [g for g in groups.values() if len(g) > 1]


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _block(v: Video, rank_of: dict[UUID, int], n_rank: int, cluster_rate: float, open_: bool = False) -> str:
    s = v.sums
    if open_:
        head = (f"rank {rank_of[v.id]} of {n_rank} by cost per purchase" if v.id in rank_of
                else f"not ranked: under {MIN_CLICKS} link clicks")
        lines = [f"=== {v.code} · {head}"]
    else:
        head = {"best": "BEST", "worst": "WORST", "middle": "MIDDLE", "unrankable": "TOO LITTLE DELIVERY TO RANK"}[v.side]
        pos = f" (rank {rank_of[v.id]} of {n_rank} rankable)" if v.id in rank_of else ""
        lines = [f"=== {v.code} · {head}{pos}"]
    views = s["video_3s_views"]
    lines.append(
        f"Delivery, last 30 days: spend ${s['spend']:,.0f}; impressions {s['impressions']:,.0f}; "
        f"3-second views {views:,.0f} ({_pct(views / s['impressions'])}); link clicks {s['link_clicks']:,.0f}; "
        f"purchases {s['conversions']:,.0f}; cost per purchase "
        + (f"${s['spend'] / s['conversions']:,.0f}" if s["conversions"] else "none")
        + f" (cluster ${1 / cluster_rate:,.0f})"
    )
    if v.funnel:
        lines.append("Funnel against the rest of the cluster (CLEAR = outside its 95% range and unusual for this cluster):")
        for st in v.funnel["steps"]:
            if st["mine"] is None or st["theirs"] is None:
                lines.append(f"  {st['key']}: {st['label']}: not measurable")
                continue
            val = (lambda x: f"${x:,.2f}") if st["key"] == "cpm" else _pct
            lines.append(
                f"  {st['key']}: {st['label']}: {val(st['mine'])} vs {val(st['theirs'])}"
                f" -> {'CLEAR' if st['clear'] else 'unclear'}"
            )
    spend = sum(v.where.values()) or 1
    where = ", ".join(f"{k} {v.where[k] / spend:.0%}" for k in sorted(v.where, key=lambda k: -v.where[k]))
    lines.append(
        f"Where it ran (share of window spend): {where or 'unknown'}"
        + (f"; Spanish-language campaign {v.spanish_spend / spend:.0%}" if v.spanish_spend else "")
    )
    if v.first_day:
        lines.append(f"First delivered {v.first_day.isoformat()}; last delivered {v.last_day.isoformat()}")
    lines.append("EVIDENCE")
    lines.append(v.evidence)
    return "\n".join(lines)


SYSTEM = """You explain why the best videos in one cluster of video ads beat the worst ones, for a creative strategist deciding what to brief next.

You get each video's numbers (already measured; trust them) and its evidence: transcript, on-screen text with times, scenes, and a few factual tags. The funnel lines say, for each step, whether this video differs CLEARLY from the rest of the cluster. Steps:
- cpm: cost to reach people. Set by the auction and where the ad ran, not by the content.
- stop: stopped in the first 3 seconds. Look at the opening: first words, first on-screen text, first scene.
- click: of those who stopped, clicked. Look at the argument, proof and call to action.
- buy: of those who clicked, bought. Look at the promise, offer and price. Every video sends people to the same page.

Rules:
1. For each BEST and WORST video, name the step that most explains its standing. You may only name a step marked CLEAR for that video. If none is clear, use "none".
2. Say what in the video sits at that step, and back it with an exact quote copied character for character from that video's evidence (transcript, on-screen text or opening words), with its time if it has one. Never paraphrase inside a quote.
3. Visually you may only speak about whether a face, the product or packaging is on screen and when. The scene lines say nothing else about the picture, so say nothing else about it.
4. The transcript and on-screen text were machine-transcribed and can contain mishearings. Burned-in captions appear as fragments that repeat the speech.
5. The messenger tag is context only. It is wrong for reaction videos that play someone else's clip.
6. Where a video ran matters. Scaling campaigns reach warmer audiences than testing ad sets. If a best and a worst video differ mainly in where they ran, say that instead of inventing a content reason.
7. Patterns: name what recurs among the BEST videos and not the WORST ("make_more"), or among the WORST and not the BEST ("stop_making"). List every video that has it, winners and losers alike. Do not name anything that describes the cluster itself (who is on screen, when the product appears): every video here shares it.
8. The quote must sit where the step happens. For stop, quote the opening (first words, or on-screen text before 0:03). For click and buy, quote what comes after the opening: the argument, proof, promise or offer. For cpm, the reason is delivery, so use source "none" and an empty quote.
9. Watch-through and hold are not measured here. Do not claim anything about how long people watched, or about a video's length helping or hurting.
10. Twins: some videos are the same script in different edits. Say what differs between them and what that suggests. One pair is a lead, not proof.
11. Write no numbers, percentages or money amounts in your own sentences. The page shows the numbers. Numbers inside exact quotes are fine.
12. Plain words, one short sentence per field, at most 25 words. No hype."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "step": {"type": "string", "enum": ["cpm", "stop", "click", "buy", "none"]},
                    "what": {"type": "string"},
                    "quote": {"type": "string"},
                    "at": {"type": "string"},
                    "source": {"type": "string", "enum": ["transcript", "on_screen_text", "opening_words", "none"]},
                },
                "required": ["id", "step", "what", "quote", "at", "source"],
            },
        },
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": ["make_more", "stop_making"]},
                    "claim": {"type": "string"},
                    "step": {"type": "string", "enum": ["cpm", "stop", "click", "buy", "none"]},
                    "videos_with": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "claim", "step", "videos_with"],
            },
        },
        "twins": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "videos": {"type": "array", "items": {"type": "string"}},
                    "difference": {"type": "string"},
                    "reading": {"type": "string"},
                },
                "required": ["videos", "difference", "reading"],
            },
        },
    },
    "required": ["videos", "patterns", "twins"],
}


@dataclass
class Brief:
    cluster: str
    span: tuple[date, date]
    videos: list[Video]
    twin_groups: list[list[Video]]
    text: str

    @property
    def by_code(self) -> dict[str, Video]:
        return {v.code: v for v in self.videos}


async def build(db: Database, org_id: UUID, experiment_id: UUID, cluster: str, open_: bool = False) -> Brief:
    span, clusters = await cluster_members(db, org_id, experiment_id)
    vs = clusters[cluster]
    await add_context(db, org_id, span, vs)
    rank(vs)
    order = {"best": 0, "worst": 1, "middle": 2, "unrankable": 3}
    vs.sort(key=lambda v: (order[v.side], -(v.shrunk or 0)))
    for i, v in enumerate(vs, 1):
        v.code = f"V{i:02d}"
    tg = twins(vs)
    in_twins = {v.id for g in tg for v in g}
    rankable = sorted([v for v in vs if v.side != "unrankable"], key=lambda v: -(v.shrunk or 0))
    rank_of = {v.id: i for i, v in enumerate(rankable, 1)}
    spend = sum(v.sums["spend"] for v in vs)
    rate = sum(v.sums["conversions"] for v in vs) / spend if spend else 0
    if open_:
        # Every video, in cost-per-purchase order; the model decides what is best and worst.
        vs.sort(key=lambda v: (v.id not in rank_of, rank_of.get(v.id, 0), -v.sums["spend"]))
        for i, v in enumerate(vs, 1):
            v.code = f"V{i:02d}"
        tg = twins(vs)
    shown = vs if open_ else [v for v in vs if v.side in ("best", "worst") or v.id in in_twins]
    parts = [
        f"CLUSTER: {cluster}. Every video here shares that description.",
        f"WINDOW: the account's last 30 days, {span[0].isoformat()} to {span[1].isoformat()}.",
        f"{len(vs)} videos delivered in the window; {len(rankable)} have enough link clicks to rank "
        + ("(on cost per purchase). All are shown, ranked first." if open_
           else "(ranked on cost per purchase). Shown: the best and worst thirds, plus any twin."),
    ]
    if tg:
        parts.append("TWINS (same script, different edits): " + "; ".join(" + ".join(v.code for v in g) for g in tg))
    parts += [_block(v, rank_of, len(rankable), rate, open_) for v in shown]
    return Brief(cluster, span, vs, tg, "\n\n".join(parts))


_NUM = re.compile(r"\d+(\.\d+)?\s*%|\$\s*\d|\b\d+(\.\d+)?x\b|\b\d{2,}\b")
_CLOCK = re.compile(r"\b\d+:\d{2}\b")
_ID = re.compile(r"V\d{2}")


def _code(x: str) -> str:
    """The model sometimes writes "id V06" for "V06"."""
    m = _ID.search(x or "")
    return m.group(0) if m else x


def _secs(clock: str) -> float | None:
    m = re.fullmatch(r"(\d+):(\d{2})", clock.strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def _where_quote(evidence: str, q: str) -> tuple[str, float | None] | None:
    """Which part of the evidence a quote came from, and its time where the part has times."""
    for line in evidence.splitlines():
        n = re.sub(r"\s+", " ", line.lower())
        if q not in n:
            continue
        if line.startswith("Opening words"):
            return "opening", 0.0
        m = re.match(r"\s+(\d+:\d{2}) ", line)
        if m:
            return "timed", _secs(m.group(1))
        if line.startswith("Transcript:"):
            return "transcript", None
        return "other", None
    return None


def check(brief: Brief, out: dict) -> tuple[dict, list[str]]:
    """Keep what passes; list what was dropped and why."""
    by = brief.by_code
    dropped: list[str] = []
    ev_norm = {c: re.sub(r"\s+", " ", v.evidence.lower()) for c, v in by.items()}

    def numbers(s: str) -> bool:
        return bool(_NUM.search(_CLOCK.sub("", s)))

    kept_v = []
    for x in out.get("videos", []):
        x = {**x, "id": _code(x["id"])}
        v = by.get(x["id"])
        if not v or v.side not in ("best", "worst"):
            dropped.append(f"{x['id']}: not a best or worst video")
            continue
        clear = {s["key"] for s in (v.funnel or {}).get("steps", []) if s["clear"]}
        if x["step"] != "none" and x["step"] not in clear:
            dropped.append(f"{x['id']}: cited '{x['step']}', which is not clear for it")
            continue
        q = re.sub(r"\s+", " ", x["quote"].lower()).strip().strip('"“”')
        if x["step"] in ("stop", "click", "buy"):
            found = _where_quote(v.evidence, q) if q else None
            if not found or q not in ev_norm[x["id"]]:
                dropped.append(f"{x['id']}: quote not found in its evidence: {x['quote'][:80]!r}")
                continue
            part, t = found
            early = part == "opening" or (part == "timed" and t is not None and t < why.OPENING_S)
            if x["step"] == "stop" and not early:
                dropped.append(f"{x['id']}: 'stop' backed by a quote from after the opening")
                continue
            if x["step"] in ("click", "buy") and early:
                dropped.append(f"{x['id']}: '{x['step']}' backed only by the opening line")
                continue
            # A line the other side also has cannot be why this one won or lost.
            other = "worst" if v.side == "best" else "best"
            shared = [c for c, o in by.items() if o.side == other and q in ev_norm[c]]
            if shared:
                dropped.append(f"{x['id']}: its quote also appears in {', '.join(shared)} ({other})")
                continue
            if part == "transcript":
                x["at"] = ""  # the transcript carries no times; any given was invented
        if numbers(x["what"]):
            dropped.append(f"{x['id']}: numbers in its own sentence")
            continue
        kept_v.append({**x, "side": v.side})

    # One script is one piece of evidence: a twin family counts once, and a family with members
    # on both sides is the twin comparison, not support for a pattern on either side.
    fam = {c: c for c in by}
    for g in brief.twin_groups:
        for v in g:
            fam[v.code] = g[0].code
    def sides_of(codes):
        out: dict[str, set[str]] = defaultdict(set)
        for c in codes:
            out[fam[c]].add(by[c].side)
        return out
    all_sides = sides_of(by)
    best = {f for f, ss in all_sides.items() if "best" in ss and "worst" not in ss}
    worst = {f for f, ss in all_sides.items() if "worst" in ss and "best" not in ss}
    kept_p = []
    for p in out.get("patterns", []):
        w = [c for c in map(_code, p["videos_with"]) if c in by]
        fams = set(sides_of(w))
        bw, ww = len(fams & best), len(fams & worst)
        fav, unf = (bw, ww) if p["kind"] == "make_more" else (ww, bw)
        nf, nu = (len(best), len(worst)) if p["kind"] == "make_more" else (len(worst), len(best))
        if fav < 2 or not nf or not nu or fav / nf <= unf / nu:
            dropped.append(f"pattern {p['claim'][:60]!r}: holds on {bw} best / {ww} worst")
            continue
        if numbers(p["claim"]):
            dropped.append(f"pattern {p['claim'][:60]!r}: numbers in its own sentence")
            continue
        kept_p.append({**p, "videos_with": w, "best_with": bw, "best_n": len(best), "worst_with": ww, "worst_n": len(worst)})

    twin_sets = [{v.code for v in g} for g in brief.twin_groups]
    kept_t = []
    for t in out.get("twins", []):
        t = {**t, "videos": [_code(c) for c in t["videos"]]}
        if not any(set(t["videos"]) <= s for s in twin_sets):
            dropped.append(f"twins {t['videos']}: not a twin group code found")
            continue
        if numbers(t["difference"]) or numbers(t["reading"]):
            dropped.append(f"twins {t['videos']}: numbers in its own sentence")
            continue
        kept_t.append(t)
    return {"videos": kept_v, "patterns": kept_p, "twins": kept_t}, dropped


async def explain(db: Database, client, model: str, org_id: UUID, experiment_id: UUID, brief: Brief) -> tuple[dict, dict]:
    """One ledgered call, no retries."""
    key = f"{experiment_id}:{brief.cluster}:{VERSION}:{brief.span[1].isoformat()}"
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=32000,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": brief.text}],
            response_format={"type": "json_schema", "json_schema": {"name": "cluster_why", "schema": SCHEMA, "strict": True}},
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        out = json.loads(choice.message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = getattr(resp, "usage", None)
    usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0}
    await model_calls.finish(db, claim.id, **usage)
    return out, usage


def payload(brief: Brief, raw: dict, kept: dict, dropped: list[str], usage: dict, model: str) -> dict:
    """What the page needs: every video's numbers beside what the model said about it."""
    def vrow(v: Video) -> dict:
        s = v.sums
        spend = sum(v.where.values()) or 1
        return {
            "code": v.code, "id": str(v.id), "name": v.name, "side": v.side,
            "spend": s["spend"], "impressions": s["impressions"], "purchases": s["conversions"],
            "link_clicks": s["link_clicks"], "hook_rate": s["video_3s_views"] / s["impressions"] if s["impressions"] else None,
            "cpa": s["spend"] / s["conversions"] if s["conversions"] else None,
            "where": {k: x / spend for k, x in v.where.items()},
            "first_day": v.first_day.isoformat() if v.first_day else None,
            "last_day": v.last_day.isoformat() if v.last_day else None,
            "steps": [{k: st.get(k) for k in ("key", "label", "mine", "theirs", "clear", "unusual")} for st in (v.funnel or {}).get("steps", [])],
        }
    return {
        "version": VERSION, "model": model, "cluster": brief.cluster,
        "span": [brief.span[0].isoformat(), brief.span[1].isoformat()],
        "videos": [vrow(v) for v in brief.videos],
        "twins": [[v.code for v in g] for g in brief.twin_groups],
        "read": kept, "dropped": dropped, "raw": raw, "usage": usage,
    }


# ---- Open comparison (2026-09-25, the user's call): the whole judgment goes to the model.
# Same stored evidence and numbers, every video in the cluster, three rules only. Code flags
# what is false and removes nothing.

SYSTEM_OPEN = """You are a senior creative strategist. Below is one cluster of video ads from one brand: every video that delivered in the last 30 days, with its numbers and its evidence (transcript, on-screen text with times, scenes, a few factual tags, where it ran).

Answer: what makes the best performers the best, and the worst the worst? Use your own judgment about which videos count as best and worst, which numbers matter, and what the patterns are. Triangulate what is in the videos with the numbers.

Three rules:
1. Quote exactly. Anything in quotation marks must be copied character for character from that video's evidence.
2. Do not claim what the data cannot show. How long people watched is not measured. The scene lines only say whether a face, product, packaging or b-roll is on screen.
3. A line or feature that a winner and a loser both have cannot be the reason one of them won or lost.

Also useful to know: the funnel lines split cost per purchase into steps against the rest of the cluster; CLEAR means outside its 95% range and unusual for this cluster. CPM is set by the auction and where the ad ran, not by the content. Scaling campaigns reach warmer audiences than testing ad sets. Twins are the same script in different edits."""

_CALL = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "why": {"type": "string"},
        "quotes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["id", "why", "quotes"],
}

# Inlined, not $ref: the gateway's strict mode is not proven on references.
SCHEMA_OPEN = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "best": {"type": "array", "items": _CALL},
        "worst": {"type": "array", "items": _CALL},
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "claim": {"type": "string"},
                    "videos_with": {"type": "array", "items": {"type": "string"}},
                    "videos_without": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["strong", "moderate", "weak"]},
                },
                "required": ["claim", "videos_with", "videos_without", "confidence"],
            },
        },
        "twins": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"videos": {"type": "array", "items": {"type": "string"}}, "reading": {"type": "string"}},
                "required": ["videos", "reading"],
            },
        },
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "best", "worst", "patterns", "twins", "caveats"],

}


async def explain_open(db: Database, client, model: str, org_id: UUID, experiment_id: UUID, brief: Brief) -> tuple[dict, dict]:
    """One ledgered call, no retries."""
    key = f"{experiment_id}:{brief.cluster}:open-v1:{brief.span[1].isoformat()}"
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=32000,
            messages=[{"role": "system", "content": SYSTEM_OPEN}, {"role": "user", "content": brief.text}],
            response_format={"type": "json_schema", "json_schema": {"name": "cluster_open", "schema": SCHEMA_OPEN, "strict": True}},
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        out = json.loads(choice.message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = getattr(resp, "usage", None)
    usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0}
    await model_calls.finish(db, claim.id, **usage)
    return out, usage


def flag_open(brief: Brief, out: dict) -> list[str]:
    """Statements that are false against the evidence or the numbers. Nothing is removed."""
    by = brief.by_code
    ev = {c: re.sub(r"\s+", " ", v.evidence.lower()) for c, v in by.items()}
    flags: list[str] = []
    rankable = sorted([v for v in brief.videos if v.side != "unrankable"], key=lambda v: -(v.shrunk or 0))
    third = max(1, len(rankable) // 3)
    top = {v.code for v in rankable[:third]}
    bottom = {v.code for v in rankable[-third:]}
    for side, calls in (("best", out.get("best", [])), ("worst", out.get("worst", []))):
        for c in calls:
            code = _code(c["id"])
            v = by.get(code)
            if not v:
                flags.append(f"{side} {c['id']}: no such video")
                continue
            if v.side == "unrankable":
                flags.append(f"{side} {code}: under {MIN_CLICKS} link clicks, too thin to call {side}")
            elif side == "best" and code in bottom or side == "worst" and code in top:
                flags.append(f"{side} {code}: it is in the opposite third by cost per purchase")
            other = {o.code for o in brief.videos if o.side == ("worst" if side == "best" else "best")}
            for q in c["quotes"]:
                qn = re.sub(r"\s+", " ", q.lower()).strip().strip('"“”')
                if qn and qn not in ev[code]:
                    flags.append(f"{side} {code}: quote not in its evidence: {q[:90]!r}")
                elif qn:
                    shared = [o for o in other if qn in ev[o]]
                    if shared:
                        flags.append(f"{side} {code}: quote also in {', '.join(sorted(shared))} (other side): {q[:60]!r}")
    fam = {c: c for c in by}
    for g in brief.twin_groups:
        for v in g:
            fam[v.code] = g[0].code
    for p in out.get("patterns", []):
        w = {_code(c) for c in p["videos_with"]} & set(by)
        wo = {_code(c) for c in p["videos_without"]} & set(by)
        if w & wo:
            flags.append(f"pattern {p['claim'][:60]!r}: videos listed as both with and without: {sorted(w & wo)}")
        fams = {fam[c] for c in w}
        bw = len({fam[c] for c in w if by[c].side == "best"})
        ww = len({fam[c] for c in w if by[c].side == "worst"})
        if len(fams) < len(w):
            flags.append(f"pattern {p['claim'][:60]!r}: {len(w)} videos but {len(fams)} distinct scripts")
        flags.append(f"pattern {p['claim'][:60]!r}: count by cost-per-purchase thirds: {bw} best / {ww} worst scripts have it")
    return flags
