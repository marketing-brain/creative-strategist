"""What code measures for a variant brief: which videos are worth iterating on, and for one
parent, its numbers, when each spoken line falls, how many viewers are left at each second,
and where the steepest drop sits. None of this comes from a model.

A parent is ranked on its first 7 days with retargeting left out: lifetime totals mix creative
quality with how long the optimiser kept paying, and retargeting buys from people who were
already going to buy. The purchase floor keeps a two-purchase CPA off the list.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from uuid import UUID

from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.briefs import words as words_mod
from cs.core.config import Settings
from cs.experiments.describe import video_evidence

MIN_LIFETIME_SPEND = 200
MIN_PURCHASES_7D = 10
RETARGETING = "retarget|remarket"
OFFER = re.compile(
    r"\d+\s*%|\boff\b|\bsale\b|guarantee|free shipping|discount|save \$", re.IGNORECASE
)


def _f(x) -> float:
    return float(x or 0)


async def candidates(db: Database, org_id: UUID, ad_account_id: UUID) -> list[dict]:
    """Videos ranked on first-7-day cost per purchase, retargeting excluded, with the purchase
    floor applied. Each row carries its last-30-day cost so wear-out is visible beside it."""
    last = await db.fetchval(
        "SELECT max(day) FROM metric_daily WHERE org_id=$1 AND ad_account_id=$2",
        org_id,
        ad_account_id,
    )
    rows = await db.fetch(
        f"""
        WITH f AS (
          SELECT f.id, f.first_day, f.duration_s, f.rep_creative_id
          FROM footage f
          JOIN footage_metric_window l ON l.footage_id = f.id AND l.window_kind = 'lifetime'
          WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = 'video'
            AND l.spend >= $3 AND f.first_day IS NOT NULL
        )
        SELECT f.id, f.first_day, f.duration_s,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id
                 ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name,
               sum(m.spend) AS spend, sum(m.conversions) AS purchases,
               sum(m.conversion_value) AS value, sum(m.impressions) AS impressions,
               sum(m.spend) FILTER (WHERE a.adset_name LIKE 'TESTING%') AS testing_spend,
               (SELECT w.spend FROM footage_metric_window w WHERE w.footage_id = f.id AND w.window_kind = 'last_30d') AS spend_30d,
               (SELECT w.purchases FROM footage_metric_window w WHERE w.footage_id = f.id AND w.window_kind = 'last_30d') AS purchases_30d
        FROM f
        JOIN footage_creative fc ON fc.footage_id = f.id
        JOIN ad a ON a.creative_id = fc.creative_id
        JOIN metric_daily m ON m.ad_id = a.id AND m.day BETWEEN f.first_day AND f.first_day + 6
        WHERE NOT a.campaign_name ~* '{RETARGETING}'
        GROUP BY f.id, f.first_day, f.duration_s, f.rep_creative_id
        """,
        org_id,
        ad_account_id,
        MIN_LIFETIME_SPEND,
    )
    out = []
    for r in rows:
        spend, purchases = _f(r["spend"]), _f(r["purchases"])
        if purchases < MIN_PURCHASES_7D or not spend:
            continue
        p30 = _f(r["purchases_30d"])
        out.append(
            {
                "footage_id": str(r["id"]),
                "name": r["name"] or "(unnamed)",
                "first_day": r["first_day"].isoformat(),
                "full_week": bool(last and (last - r["first_day"]).days >= 6),
                "duration_s": _f(r["duration_s"]),
                "spend_7d": round(spend, 2),
                "purchases_7d": int(purchases),
                "cpa_7d": round(spend / purchases, 2),
                "roas_7d": round(_f(r["value"]) / spend, 2),
                "testing_share_7d": round(_f(r["testing_spend"]) / spend, 2),
                "cpa_30d": round(_f(r["spend_30d"]) / p30, 2) if p30 else None,
            }
        )
    out.sort(key=lambda c: c["cpa_7d"])
    return out


def speech_spans(audio_video: bytes, duration: float) -> list[tuple[float, float]]:
    """Stretches with sound above the pause threshold, from ffmpeg's silence detector."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.mp4"
        src.write_bytes(audio_video)
        err = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(src),
                "-af",
                "silencedetect=noise=-38dB:d=0.15",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        ).stderr
    marks = [(k, float(v)) for k, v in re.findall(r"silence_(start|end): ([0-9.]+)", err)]
    spans, t = [], 0.0
    for k, v in marks:
        if k == "start":
            if v - t > 0.15:
                spans.append((t, v))
        else:
            t = v
    if duration - t > 0.15 and (not marks or marks[-1][0] == "end"):
        spans.append((t, duration))
    return spans


def time_sentences(transcript: str, spans: list[tuple[float, float]]) -> list[dict]:
    """Approximate start and end of each sentence. People pause between sentences, so each
    sentence is given a run of whole speech stretches, chosen so every sentence's share of the
    speaking time best matches its share of the words (a small dynamic programme). Good to
    about a second; an editor confirms by ear."""
    sentences = [s.strip() for s in re.split(r"(?<=[.?!])\s+", transcript or "") if s.strip()]
    if not sentences or not spans:
        return []
    n, m = len(sentences), len(spans)
    if n > m:
        # More sentences than pauses: spread evenly over the speaking time instead.
        spans = [(spans[0][0], spans[-1][1])]
        total = spans[0][1] - spans[0][0]
        chars = sum(len(x) for x in sentences)
        out, t = [], spans[0][0]
        for x in sentences:
            d = total * len(x) / chars
            out.append({"start": round(t, 1), "end": round(t + d, 1), "text": x})
            t += d
        return out
    rate = sum(b - a for a, b in spans) / sum(len(x) for x in sentences)
    speak = [0.0]
    for a, b in spans:
        speak.append(speak[-1] + b - a)
    INF = float("inf")
    # best[i][j]: sentences[:i] placed on spans[:j]
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(1, n + 1):
        want = len(sentences[i - 1]) * rate
        for j in range(i, m - (n - i) + 1):
            for k in range(i - 1, j):
                if best[i - 1][k] == INF:
                    continue
                c = best[i - 1][k] + (speak[j] - speak[k] - want) ** 2
                if c < best[i][j]:
                    best[i][j], back[i][j] = c, k
    cuts, j = [m], m
    for i in range(n, 0, -1):
        j = back[i][j]
        cuts.append(j)
    cuts.reverse()
    return [
        {
            "start": round(spans[cuts[i]][0], 1),
            "end": round(spans[cuts[i + 1] - 1][1], 1),
            "text": x,
        }
        for i, x in enumerate(sentences)
    ]


def steepest_drop(
    curve: list[tuple[float, float]], after: float = 5.0, floor: float = 0.02
) -> dict | None:
    """The stretch that loses viewers fastest compared with the stretches either side of it.
    Every video sheds viewers fastest at the start, so a raw per-second rate would always
    point at the opening; the comparison with the neighbours finds a line that drives
    people away."""
    segs = []
    for (t0, v0), (t1, v1) in zip(curve, curve[1:], strict=False):
        if t1 > t0 and v0 > 0 and v1 > 0:
            segs.append(
                {
                    "start": t0,
                    "end": t1,
                    "from": round(v0, 3),
                    "to": round(v1, 3),
                    "loss_per_s": 1 - (v1 / v0) ** (1 / (t1 - t0)),
                }
            )
    best = None
    for i in range(1, len(segs) - 1):
        s = segs[i]
        if s["start"] < after or s["from"] < floor:
            continue
        around = (segs[i - 1]["loss_per_s"] + segs[i + 1]["loss_per_s"]) / 2
        if around <= 0:
            continue
        ratio = s["loss_per_s"] / around
        if best is None or ratio > best["ratio"]:
            best = {
                **s,
                "loss_per_s": round(s["loss_per_s"], 3),
                "around_per_s": round(around, 3),
                "ratio": round(ratio, 2),
            }
    return best if best and best["ratio"] >= 1.5 else None


async def parent_facts(
    db: Database,
    blobs: BlobStore,
    org_id: UUID,
    ad_account_id: UUID,
    footage_id: UUID,
    settings: Settings | None = None,
) -> dict:
    """Everything code measures about the parent. With `settings`, one paid transcription call
    places sentence boundaries by the words; without it, by pauses alone."""
    f = await db.fetchrow(
        "SELECT id, duration_s, rep_creative_id, first_day FROM footage WHERE id=$1 AND org_id=$2 AND format='video'",
        footage_id,
        org_id,
    )
    if f is None:
        raise ValueError("not a video in this org")
    stats = next(
        (
            c
            for c in await candidates(db, org_id, ad_account_id)
            if c["footage_id"] == str(footage_id)
        ),
        None,
    )
    ev = await video_evidence(db, f)
    tags = await db.fetch(
        """
        SELECT t.dimension, t.value, t.t_start_s FROM tag t
        WHERE t.tag_run_id = (SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1
                              AND source_tag_run_id IS NOT NULL LIMIT 1)
          AND t.dimension IN ('transcript', 'overlay_texts')
        ORDER BY t.t_start_s NULLS FIRST, t.ordinal
        """,
        footage_id,
    )
    transcript = next((t["value"] for t in tags if t["dimension"] == "transcript"), "")
    overlays = [
        {"t": _f(t["t_start_s"]), "text": t["value"]}
        for t in tags
        if t["dimension"] == "overlay_texts"
    ]
    duration = _f(f["duration_s"])
    path = await db.fetchval(
        "SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id WHERE c.id=$1",
        f["rep_creative_id"],
    )
    video = await blobs.get(path) if path else b""
    spans = speech_spans(video, duration) if video else []
    words = (
        await words_mod.transcribe(db, settings, org_id, footage_id, video)
        if settings and video
        else None
    ) or []
    timed = words_mod.sentences(transcript, words, spans, duration) if words else []
    if not timed:
        timed = [
            {
                **x,
                "i": k,
                "cut_before": x["start"],
                "cut_after": x["end"],
                "clean_before": False,
                "clean_after": False,
            }
            for k, x in enumerate(time_sentences(transcript, spans))
        ]
    curve = [
        (_f(r["start_s"]), _f(r["share"]))
        for r in await db.fetch(
            "SELECT start_s, share FROM play_curve_point WHERE footage_id=$1 AND window_kind='lifetime'"
            " AND start_s <= $2 ORDER BY bucket",
            footage_id,
            duration,
        )
    ]
    scenes = [
        {
            "start": _f(r["t_start_s"]),
            "end": _f(r["t_end_s"]) if r["t_end_s"] is not None else duration,
            "label": r["label"],
        }
        for r in await db.fetch(
            "SELECT t_start_s, t_end_s, label FROM scene WHERE footage_id=$1 ORDER BY ordinal",
            footage_id,
        )
    ]
    end_text = " ".join(o["text"] for o in overlays if o["t"] >= duration - 6)
    name = await db.fetchval(
        "SELECT name FROM ad WHERE creative_id=$1 ORDER BY first_seen_at, id LIMIT 1",
        f["rep_creative_id"],
    )
    return {
        "footage_id": str(footage_id),
        "name": name,
        "duration_s": duration,
        "stats": stats,
        "sentences": timed,
        "timing": "words" if words else "pauses",
        "words": words,
        "source_path": path,
        "transcript": transcript,
        "overlays": overlays,
        "scenes": scenes,
        "curve": [[t, round(v, 4)] for t, v in curve],
        "steepest_drop": steepest_drop(curve),
        "end_card_offer": bool(OFFER.search(end_text)),
        "evidence": ev.text,
        "measured_on": date.today().isoformat(),
    }


def share_at(curve: list[list[float]], t: float) -> float | None:
    for s, v in curve:
        if s == t:
            return v
    return None
