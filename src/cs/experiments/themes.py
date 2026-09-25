"""The buyer's own themes, and one model call per video that sorts it into them.

The themes are the buyer's (2026-09-25), not discovered: the model picks one per axis or
'other', from the information already held about the video (transcript, on-screen text,
scenes, a few factual tags, ad copy — the same evidence as cs.experiments.describe). One run,
no majority vote: the check is the buyer scrolling the groups, and a definition that sorts
badly is sharpened and re-run.
"""

from __future__ import annotations

import json
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database
from cs.experiments.describe import Evidence

PURPOSE = "experiments.themes"
VERSION = "themes-v1"

FORMATS: dict[str, str] = {
    "cryptic_text": "Cryptic Text: the message is carried by on-screen text over unrelated or "
    "moody footage; no one speaks to camera.",
    "interviews": "Interviews: someone is asked questions on camera — street interviews, podcast "
    "clips, Q&A.",
    "animated": "Animated: drawn, motion-graphic or AI-animated visuals carry the ad, not filmed "
    "people.",
    "mashups": "Mashups (short cuts): many quick cuts stitched from different clips, often with a "
    "voiceover.",
    "third_party_reference": "3P Video Reference: reuses or reacts to someone else's video — "
    "stitches, duets, reposted TikToks, news or podcast clips.",
    "body_closeups": "Body close-ups: tight shots of the body (skin, face, hands, belly) are the "
    "main visual.",
    "kitchen": "Kitchen videos: filmed in a kitchen, usually a person talking while making or "
    "taking something.",
    "other": "Other: none of the above fits.",
}
CONTENTS: dict[str, str] = {
    "medical_advice": "Medical Advice: an expert, doctor or clinical explanation is the reason to "
    "believe (mechanisms, cortisol, absorption).",
    "competitor_diss": "Competitor Diss: argues against an alternative — melatonin, Benadryl or "
    "ZzzQuil, cheaper or 'wrong' magnesium.",
    "conundrum": "Conundrum: opens a puzzle or a question the viewer can't answer ('why do you "
    "wake at 3am?') and then resolves it.",
    "living_the_problem": "Living the problem: shows or narrates the symptom as it is lived (the "
    "3am wake, exhaustion, cravings) before the fix.",
    "other": "Other: none of the above fits.",
}
FUNNEL: dict[str, str] = {
    "unaware": "Unaware: the viewer does not yet know they have the problem; the ad makes them "
    "realise it.",
    "problem_aware": "Problem aware: the viewer feels the problem but isn't looking at solutions; "
    "the ad dwells on the problem and its cause.",
    "solution_aware": "Solution aware: the viewer knows solutions like this exist but not this "
    "product; the ad argues why this kind of solution, or this form, is better.",
    "product_aware": "Product aware: the viewer knows this product or brand; the ad builds trust "
    "or answers objections about it.",
    "most_aware": "Most aware: the viewer is ready to buy; the ad is mainly an offer, price or "
    "urgency.",
}
FUNNEL_ROLLUP = {
    "unaware": "top", "problem_aware": "top", "solution_aware": "middle",
    "product_aware": "bottom", "most_aware": "bottom",
}
LABELS = {
    "format": {k: v.split(":")[0] for k, v in FORMATS.items()},
    "content": {k: v.split(":")[0] for k, v in CONTENTS.items()},
    "funnel_stage": {k: v.split(":")[0] for k, v in FUNNEL.items()},
}

SYSTEM = """You sort video ads from a supplement brand into a creative strategist's own \
themes. For each axis pick exactly one value from the list given; pick 'other' only when none \
fits. Judge from the evidence, not from what ads like this usually are. Each reason is one \
short sentence (under 20 words) pointing at the evidence that decided it."""


def _axis(values: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in values.items())


def user_text(ev: Evidence) -> str:
    return (
        f"EVIDENCE\n{ev.text}\n\n"
        f"FORMAT — how the ad is made. One of:\n{_axis(FORMATS)}\n\n"
        f"CONTENT — what the ad argues. One of:\n{_axis(CONTENTS)}\n\n"
        f"FUNNEL STAGE — what the ad assumes the viewer already knows when it starts. One of:\n"
        f"{_axis(FUNNEL)}"
    )


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "format": {"type": "string", "enum": list(FORMATS)},
        "format_reason": {"type": "string"},
        "content": {"type": "string", "enum": list(CONTENTS)},
        "content_reason": {"type": "string"},
        "funnel_stage": {"type": "string", "enum": list(FUNNEL)},
        "funnel_reason": {"type": "string"},
    },
    "required": ["format", "format_reason", "content", "content_reason", "funnel_stage", "funnel_reason"],
}


async def assign(db: Database, client, model: str, org_id: UUID, experiment_id: UUID, ev: Evidence) -> dict:
    """One ledgered call; stores one label per axis. No retries: a failure is recorded and
    raised, and the caller moves on."""
    claim = await model_calls.claim(db, org_id, PURPOSE, f"{experiment_id}:{ev.footage_id}:{VERSION}", model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_text(ev)}],
            response_format={"type": "json_schema", "json_schema": {"name": "themes", "schema": SCHEMA, "strict": True}},
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        out = json.loads(choice.message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = getattr(resp, "usage", None)
    await model_calls.finish(
        db, claim.id,
        input_tokens=getattr(u, "prompt_tokens", 0) or 0,
        output_tokens=getattr(u, "completion_tokens", 0) or 0,
    )
    await db.executemany(
        "INSERT INTO experiment_label (experiment_id, footage_id, axis, value, reason)"
        " VALUES ($1, $2, $3, $4, $5)"
        " ON CONFLICT (experiment_id, footage_id, axis) DO UPDATE SET value=EXCLUDED.value,"
        " reason=EXCLUDED.reason, created_at=now()",
        [
            (experiment_id, ev.footage_id, "format", out["format"], out["format_reason"]),
            (experiment_id, ev.footage_id, "content", out["content"], out["content_reason"]),
            (experiment_id, ev.footage_id, "funnel_stage", out["funnel_stage"], out["funnel_reason"]),
        ],
    )
    return {"input_tokens": getattr(u, "prompt_tokens", 0) or 0, "output_tokens": getattr(u, "completion_tokens", 0) or 0}
