"""Where each sentence of a video starts and ends, and where it is safe to cut.

Two measurements, each used for what it is good at:
- a transcription model's word timestamps say WHICH words sit around a boundary, but they drift
  by a few tenths of a second and often spill into pauses;
- ffmpeg's silence detector says exactly WHERE the pauses are, but not which words they separate.

So each sentence boundary is placed in the pause nearest the model's word boundary. A cut at a
pause's midpoint never splits a word and never leaves a stray "And" or "Look," behind, which the
pause-only timing did on V03 (2026-09-25).

The call is one paid request per video, recorded in the model-call ledger and never retried; a
video already transcribed reuses its words.
"""

from __future__ import annotations

import base64
import difflib
import re
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

import httpx

from cs.core import model_calls
from cs.core.config import Settings
from cs.core.db import Database

PURPOSE = "briefs.words"
MODEL = "openai/whisper-1"
EDGE = 0.5  # how far past a word boundary to look for a pause

_NUM = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "twenty": "20",
    "thirty": "30",
    "hundred": "100",
    "three hundred": "300",
}


def _tokens(s: str) -> list[str]:
    out = []
    for t in re.findall(r"[a-z0-9']+", s.lower().replace("’", "'")):
        t = t.strip("'")
        out.append(_NUM.get(t, t))
    return [t for t in out if t]


def audio_mp3(video: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.mp4", Path(tmp) / "out.mp3"
        src.write_bytes(video)
        subprocess.run(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-b:a",
                "48k",
                str(out),
            ],
            check=True,
            timeout=120,
        )
        return out.read_bytes()


async def transcribe(
    db: Database, settings: Settings, org_id: UUID, footage_id: UUID, video: bytes
) -> list[dict] | None:
    """Word timestamps for a video, or None when the call fails or was already tried and failed."""
    key = f"{footage_id}:{MODEL}"
    cached = await db.fetchval(
        "SELECT facts->'words' FROM creative_brief WHERE footage_id=$1 AND facts ? 'words'"
        " AND jsonb_array_length(facts->'words') > 0 LIMIT 1",
        footage_id,
    )
    if cached:
        return cached
    if await model_calls.attempts_for(db, PURPOSE, key):
        return None
    claim = await model_calls.claim(db, org_id, PURPOSE, key, MODEL)
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            r = await c.post(
                f"{settings.ai_gateway_base_url}/v4/ai/transcription-model",
                headers={
                    "Authorization": f"Bearer {settings.ai_gateway_api_key}",
                    "ai-gateway-protocol-version": "0.0.1",
                    "ai-transcription-model-specification-version": "4",
                    "ai-model-id": MODEL,
                },
                json={
                    "audio": base64.b64encode(audio_mp3(video)).decode(),
                    "mediaType": "audio/mpeg",
                    "providerOptions": {"openai": {"timestampGranularities": ["word"]}},
                },
            )
            r.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - timing falls back to pauses alone
        await model_calls.fail(db, claim.id, str(exc))
        return None
    await model_calls.finish(db, claim.id)
    return [
        {
            "w": s["text"],
            "start": round(float(s["startSecond"]), 2),
            "end": round(float(s["endSecond"]), 2),
        }
        for s in r.json().get("segments") or []
    ]


def _pauses(spans: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    """The gaps between stretches of speech, including before the first and after the last."""
    edges = [(0.0, spans[0][0])] if spans and spans[0][0] > 0 else []
    edges += [(a1, b0) for (_, a1), (b0, _) in zip(spans, spans[1:], strict=False)]
    if spans and spans[-1][1] < duration:
        edges.append((spans[-1][1], duration))
    return edges


def sentences(
    transcript: str, words: list[dict], spans: list[tuple[float, float]], duration: float
) -> list[dict]:
    """Each sentence of the transcript with its speech start and end (from the words) and the
    safe cut points before and after it (the middle of the pause at each boundary)."""
    sents = [s.strip() for s in re.split(r"(?<=[.?!])\s+", transcript or "") if s.strip()]
    if not sents or not words:
        return []
    ttoks, owner = [], []
    for i, s in enumerate(sents):
        for t in _tokens(s):
            ttoks.append(t)
            owner.append(i)
    wtoks = [(_tokens(w["w"]) or [""])[0] for w in words]
    sm = difflib.SequenceMatcher(None, ttoks, wtoks, autojunk=False)
    hits: dict[int, list[int]] = {}
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            hits.setdefault(owner[a + k], []).append(b + k)
    out = []
    for i, s in enumerate(sents):
        ws = hits.get(i)
        out.append(
            {
                "i": i,
                "text": s,
                "start": words[min(ws)]["start"] if ws else None,
                "end": words[max(ws)]["end"] if ws else None,
            }
        )
    # A sentence the words could not place sits between its neighbours.
    for k, s in enumerate(out):
        if s["start"] is None:
            prev = next((x["end"] for x in reversed(out[:k]) if x["end"] is not None), 0.0)
            nxt = next((x["start"] for x in out[k + 1 :] if x["start"] is not None), duration)
            s["start"], s["end"], s["unplaced"] = prev, nxt, True
    pauses = _pauses(spans, duration)

    last = 0.0

    def cut_between(end: float, start: float) -> tuple[float, bool]:
        # The pause nearest the words' own boundary, after the previous cut.
        lo, hi, target = end - EDGE, start + EDGE, (end + start) / 2
        cands = [p for p in pauses if p[1] > lo and p[0] < hi and (p[0] + p[1]) / 2 > last + 0.2]
        if not cands:
            return round(max(target, last + 0.2), 2), False
        p = min(cands, key=lambda p: abs((p[0] + p[1]) / 2 - target))
        return round((p[0] + p[1]) / 2, 2), True

    for k, s in enumerate(out):
        if k == 0:
            s["cut_before"], s["clean_before"] = 0.0, True
        else:
            t, ok = cut_between(out[k - 1]["end"], s["start"])
            last = t
            s["cut_before"], s["clean_before"] = t, ok
            out[k - 1]["cut_after"], out[k - 1]["clean_after"] = t, ok
    out[-1]["cut_after"], out[-1]["clean_after"] = round(duration, 2), True
    for s in out:
        s["start"], s["end"] = round(s["start"], 2), round(s["end"], 2)
    return out
