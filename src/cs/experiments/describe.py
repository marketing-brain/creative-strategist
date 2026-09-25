"""Describe each file on six questions (format, angle, tone, funnel stage, messenger, hook),
in plain words, with no labels offered. Used by the experiments' describe step.

What goes in is evidence, never an earlier interpretation. A video brings its transcript, its
first spoken words, its on-screen text with times, its scenes and a handful of near-factual
tags (how it was shot, who is on screen, whether anyone talks). The old taxonomy's readings
(angles, registers, hook devices, the concept summary) stay out: they would pull the
descriptions toward the vocabulary this is meant to replace. An image brings the image itself
and its ad copy.

What comes out is one or two sentences per axis, written at the level a strategist groups
ads, so two ads of the same kind read alike and incidental detail (names, flavours, exact
numbers) does not split them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from cs.core import model_calls
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.tagging.tagger import build_content

PROMPT_VERSION = "describe-v1"
# The ledger name predates the move from cs.clusters; kept so earlier paid calls still count.
PURPOSE = "clusters.describe"

# Near-factual tags that measured consistent (cs.insights.account_read.reliability); anything
# interpretive is left out on purpose.
FACT_DIMS = ("production", "narration", "setting", "cast", "captions", "music", "messenger")

# One field per question. The guidance says where its edges are, so an answer about tone
# does not drift into the argument.
QUESTIONS: dict[str, str] = {
    "format": (
        "How is the ad made and assembled? The kind of footage or artwork, how it was filmed "
        "or produced, and how it is edited together. Not what it says."
    ),
    "angle": (
        "What argument does the ad make to get someone to buy? The core reason or story it "
        "offers, in terms of the viewer's problem and why this product answers it. Not the "
        "style or the mood."
    ),
    "tone": (
        "What is the emotional register? How it feels to watch: the energy, attitude and "
        "feeling it carries. Not what it argues."
    ),
    "funnel_stage": (
        "What does the ad assume the viewer already knows or believes when it starts? About "
        "their own problem, about ways of solving it, and about this brand or product."
    ),
    "messenger": (
        "Who is delivering the message, and in what role? The kind of person or voice (not "
        "their name) and what gives them standing to say it. If nobody speaks or appears, say "
        "what carries the message instead."
    ),
    "hook": (
        "What do the first three seconds do to stop someone scrolling? What is seen, said or "
        "written in that moment, and why it would make someone stay."
    ),
}

SYSTEM = """You are a senior creative strategist reviewing one ad from a supplement brand's \
Meta account. You will answer several separate questions about it. Your answers will be \
compared across hundreds of ads to find the natural groups, so:

- Describe; do not label. Use plain words, not a category name or marketing jargon.
- Write at the level a strategist would group ads by. Capture the kind of thing this ad \
does, and leave out incidental specifics (people's names, exact numbers, flavours, dates) \
that would make two ads of the same kind read differently.
- Keep each answer to its own question. Do not repeat the argument when asked about tone, \
or the tone when asked about format.
- One or two sentences per answer. Base everything on the evidence given; if the evidence \
does not show something, say so briefly rather than guessing."""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {k: {"type": "string", "description": q} for k, q in QUESTIONS.items()},
    "required": list(QUESTIONS),
}


@dataclass
class Evidence:
    footage_id: UUID
    format: str
    text: str
    image: tuple[bytes, str] | None = None


def _clock(s: float | None) -> str:
    if s is None:
        return "?"
    return f"{int(s) // 60}:{int(s) % 60:02d}"


async def _copy(db: Database, creative_id: UUID | None) -> str:
    if creative_id is None:
        return ""
    r = await db.fetchrow("SELECT headline, primary_text FROM creative WHERE id=$1", creative_id)
    if not r:
        return ""
    parts = []
    if r["headline"]:
        parts.append(f"Headline: {r['headline']}")
    if r["primary_text"]:
        parts.append(f"Primary text: {r['primary_text']}")
    return "\n".join(parts)


async def video_evidence(db: Database, f: asyncpg.Record) -> Evidence:
    run = await db.fetchval(
        "SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1"
        " AND source_tag_run_id IS NOT NULL LIMIT 1",
        f["id"],
    )
    text_tags = await db.fetch(
        "SELECT dimension, value, t_start_s FROM tag WHERE tag_run_id=$1"
        " AND dimension IN ('transcript', 'hook_verbatim', 'overlay_texts')"
        " ORDER BY dimension, t_start_s NULLS FIRST, ordinal",
        run,
    )
    facts = await db.fetch(
        "SELECT dimension, value FROM footage_attribute WHERE footage_id=$1"
        " AND dimension = ANY($2::text[]) ORDER BY dimension",
        f["id"],
        list(FACT_DIMS),
    )
    scenes = await db.fetch(
        "SELECT t_start_s, label, raw FROM scene WHERE footage_id=$1 ORDER BY ordinal", f["id"]
    )
    transcript = next((t["value"] for t in text_tags if t["dimension"] == "transcript"), "")
    first_words = next((t["value"] for t in text_tags if t["dimension"] == "hook_verbatim"), "")
    overlays = [t for t in text_tags if t["dimension"] == "overlay_texts"]
    lines = [f"A video ad, {round(float(f['duration_s'] or 0))} seconds long."]
    if facts:
        lines.append("Facts: " + "; ".join(f"{r['dimension']} = {r['value']}" for r in facts))
    lines.append(f"Opening words, spoken or on screen: {first_words or '(none)'}")
    if overlays:
        lines.append("On-screen text:")
        lines += [f"  {_clock(o['t_start_s'])} {o['value']}" for o in overlays]
    if scenes:
        lines.append("Scenes:")
        lines += [f"  {_clock(s['t_start_s'])} {s['raw'] or s['label']}" for s in scenes]
    lines.append(f"Transcript: {transcript or '(no speech)'}")
    if copy := await _copy(db, f["rep_creative_id"]):
        lines.append(f"Ad copy shown with it:\n{copy}")
    return Evidence(f["id"], "video", "\n".join(lines))


async def image_evidence(db: Database, blobs: BlobStore, f: asyncpg.Record) -> Evidence:
    a = await db.fetchrow(
        "SELECT a.path, a.mime FROM creative c JOIN asset a ON a.id = c.primary_asset_id"
        " WHERE c.id=$1",
        f["rep_creative_id"],
    )
    image = (await blobs.get(a["path"]), a["mime"] or "image/jpeg") if a else None
    lines = ["A static image ad (shown above)."]
    if copy := await _copy(db, f["rep_creative_id"]):
        lines.append(f"Ad copy shown with it:\n{copy}")
    return Evidence(f["id"], "image", "\n".join(lines), image)


def user_text(ev: Evidence) -> str:
    return (
        f"EVIDENCE\n{ev.text}\n\nAnswer each question about this ad:\n"
        + "\n".join(f"- {k}: {q}" for k, q in QUESTIONS.items())
    )


async def existing(db: Database, footage_id: UUID) -> dict | None:
    v = await db.fetchval(
        "SELECT answers FROM footage_description WHERE footage_id=$1 AND prompt_version=$2",
        footage_id,
        PROMPT_VERSION,
    )
    return v


async def describe(db: Database, client, model: str, org_id: UUID, ev: Evidence) -> dict:
    """One paid call, claimed in the ledger first. No retries: a failure is recorded and
    raised, and the caller decides."""
    claim = await model_calls.claim(db, org_id, PURPOSE, f"{ev.footage_id}:{PROMPT_VERSION}", model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=8192,
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_content([ev.image] if ev.image else [], user_text(ev))},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "description", "schema": SCHEMA, "strict": True},
            },
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        answers = json.loads(choice.message.content or "{}")
        if set(answers) != set(QUESTIONS):
            raise RuntimeError(f"missing answers: {set(QUESTIONS) - set(answers)}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    usage = getattr(resp, "usage", None)
    tin = getattr(usage, "prompt_tokens", 0) or 0
    tout = getattr(usage, "completion_tokens", 0) or 0
    await model_calls.finish(db, claim.id, input_tokens=tin, output_tokens=tout)
    await db.execute(
        "INSERT INTO footage_description (footage_id, prompt_version, model, answers,"
        " input_tokens, output_tokens) VALUES ($1, $2, $3, $4, $5, $6)"
        " ON CONFLICT (footage_id, prompt_version) DO NOTHING",
        ev.footage_id,
        PROMPT_VERSION,
        model,
        answers,
        tin,
        tout,
    )
    return answers
