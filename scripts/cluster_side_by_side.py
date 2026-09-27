"""Experiment 5, the simple read: the 3 best and 3 worst videos in a cluster, side by side.

Code picks the six by cost per purchase (last 30 days, at least 100 link clicks), cuts each to
its first 30 seconds, and sends the clips to Gemini in one call with each video's numbers and
its retention curve to 15 seconds. Gemini says what the winners do that the losers don't,
pointing to timestamps; a person checks by watching. No tags, no automated checks.

Usage:
    uv run python scripts/cluster_side_by_side.py --org <uuid> --cluster "Talking head · Late reveal (>10s)" [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import openai

from cs.core import model_calls
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.experiments import cluster_why as cw

SLUG = "tag-clusters"
PURPOSE = "experiments.side_by_side"
VERSION = "sbs-v1"
CLIP_S = 30
N = 3

SYSTEM = """You are a senior performance creative strategist for a direct-to-consumer supplement brand.
You are shown the first 30 seconds of six video ads from one group of similar ads: the three with
the lowest cost per purchase (winners) and the three with the highest (losers), each with its
numbers. Explain what the winners do that the losers don't, and the reverse.

Rules:
- Point to what you see or hear, with the video label and a timestamp (m:ss) for every point,
  so a person can check it by watching.
- A point only counts if it separates the groups: say which winners have it and which losers
  lack it. If a winner and a loser share something, it is not the reason.
- Use the numbers. The retention curve shows the share of viewers still watching at each
  second; tie what happens on screen to where the curves differ.
- CPM is set by the auction and where the ad ran, not by the video. If delivery explains a
  result better than the content does, say so.
- You only see the first 30 seconds. Do not guess about the rest.
- Do not consider frame shape or aspect ratio; it is not a factor here.
- Plain words. If nothing clearly separates the groups, say that."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "differences": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "point": {"type": "string"},
                "favours": {"type": "string", "enum": ["winners", "losers"]},
                "have_it": {"type": "array", "items": {"type": "string"}},
                "lack_it": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {"video": {"type": "string"}, "at": {"type": "string"}, "what": {"type": "string"}},
                    "required": ["video", "at", "what"]}},
            },
            "required": ["point", "favours", "have_it", "lack_it", "evidence"]}},
        "per_video": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"video": {"type": "string"}, "read": {"type": "string"}},
            "required": ["video", "read"]}},
        "caveats": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "differences", "per_video", "caveats"],
}


def clip(data: bytes, seconds: int) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        src, out = Path(d, "in.mp4"), Path(d, "out.mp4")
        src.write_bytes(data)
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-t", str(seconds),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", str(out)],
            check=True,
        )
        return out.read_bytes()


def card(label: str, v: cw.Video, curve: list) -> str:
    s = v.sums
    spend = sum(v.where.values()) or 1
    steps = {st["key"]: st for st in (v.funnel or {}).get("steps", [])}

    def pct(k):
        st = steps.get(k)
        return f"{st['mine']:.1%} (rest of group {st['theirs']:.1%})" if st and st["mine"] is not None else "n/a"

    where = ", ".join(f"{k} {x / spend:.0%}" for k, x in sorted(v.where.items(), key=lambda kv: -kv[1]))
    return "\n".join([
        f"VIDEO {label}: {round(v.duration_s or 0)}s long; the clip below is its first {CLIP_S}s.",
        f"  last 30 days: spend ${s['spend']:,.0f}, purchases {s['conversions']:.0f}, cost per purchase "
        f"${s['spend'] / s['conversions']:,.0f}, CPM ${s['spend'] / s['impressions'] * 1000:,.0f}",
        f"  stopped in first 3s: {pct('stop')}; clicked, of those who stopped: {pct('click')}; "
        f"bought, of those who clicked: {pct('buy')}",
        f"  where it ran: {where}" + (f"; Spanish-language campaign {v.spanish_spend / spend:.0%}" if v.spanish_spend else ""),
        "  still watching: " + (" ".join(f"{r['start_s']}s {r['share']:.0%}" for r in curve) if curve else "no curve"),
    ])


async def run(org: UUID, cluster: str, dry: bool) -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    blobs = blob_store_from_settings(s)
    exp = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org, SLUG)
    brief = await cw.build(db, org, exp, cluster, open_=True)
    ranked = sorted([v for v in brief.videos if v.side != "unrankable" and v.sums["conversions"]],
                    key=lambda v: v.sums["spend"] / v.sums["conversions"])
    picks = [(f"W{i}", v) for i, v in enumerate(ranked[:N], 1)] + [(f"L{i}", v) for i, v in enumerate(reversed(ranked[-N:]), 1)]
    content: list[dict] = [{"type": "text", "text": f"Group: {cluster}. Winners are W1-W3 (lowest cost per purchase first); losers are L1-L3 (highest first)."}]
    meta = []
    for label, v in picks:
        curve = await db.fetch(
            "SELECT start_s, share FROM play_curve_point WHERE footage_id=$1 AND window_kind='last_30d'"
            " AND start_s <= 15 ORDER BY bucket", v.id)
        path = await db.fetchval(
            "SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id WHERE c.id=$1", v.rep_creative_id)
        text = card(label, v, curve)
        print(text, "\n  name:", v.name, "\n")
        meta.append({"label": label, "footage_id": str(v.id), "name": v.name, "path": path,
                     "cpa": v.sums["spend"] / v.sums["conversions"], "card": text})
        if dry:
            continue
        data = clip(await blobs.get(path), CLIP_S)
        content.append({"type": "text", "text": text})
        content.append({"type": "file", "file": {"data": base64.standard_b64encode(data).decode(), "media_type": "video/mp4"}})
    content.append({"type": "text", "text": "Based on the six clips and numbers above: what makes the winners the winners and the losers the losers?"})
    if dry:
        await db.close()
        return
    model = s.account_read_model
    client = openai.AsyncOpenAI(api_key=s.ai_gateway_api_key, base_url=f"{s.ai_gateway_base_url}/v1", max_retries=0, timeout=900)
    claim = await model_calls.claim(db, org, PURPOSE, f"{exp}:{cluster}:{VERSION}", model)
    try:
        resp = await client.chat.completions.create(
            model=model, max_tokens=16000,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            response_format={"type": "json_schema", "json_schema": {"name": "side_by_side", "schema": SCHEMA, "strict": True}},
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = resp.usage
    await model_calls.finish(db, claim.id, input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens)
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
        exp, f"side_by_side:{cluster}",
        {"version": VERSION, "model": model, "usage": {"in": u.prompt_tokens, "out": u.completion_tokens},
         "videos": meta, "answer": out},
    )
    print(f"tokens in {u.prompt_tokens:,} out {u.completion_tokens:,}\n")
    print(json.dumps(out, indent=2, ensure_ascii=False))
    await db.close()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--org", type=UUID, required=True)
    p.add_argument("--cluster", required=True)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    asyncio.run(run(a.org, a.cluster, a.dry_run))


if __name__ == "__main__":
    main()
