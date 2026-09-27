"""A time-coded log of one video: what is seen and heard, second by second. One model call.

The model watches the file (frames and sound) and logs five tracks, the way an edit timeline
shows them: shots, speech, on-screen text, audio events and product visibility. It describes
and never judges, and it gets no performance numbers, no ad name and no earlier tags, so the
log cannot lean toward a story about why the video won or lost. Values come from fixed lists so
events add up across videos; anything derived (first product shot, first music change, cuts in
the first 3 seconds) is computed here from the tracks, never asked for.

The log exists to be laid over Meta's retention curve (play_curve_point), which is per second
only to 15 seconds, so the prompt asks for every change in that stretch.

Checks, run on every log and stored beside it: times inside the video, shots covering it end to
end, and agreement with what the tagger already recorded (transcript words, on-screen text,
first product scene).
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database
from cs.tagging.tagger import build_content

PURPOSE = "experiments.timeline"
VERSION = "timeline-v2"

SYSTEM = """<role>
You are a video editor logging an ad for an analyst. You write an exact, time-coded log of
everything a viewer sees and hears, the way an edit timeline shows it. You describe. You never
judge or interpret.
</role>

<rules>
1. Log only what can be seen or heard. No opinions about quality, persuasion or intent: never
   "engaging", "strong hook", "to build trust".
2. Times are seconds from the start of the video, written as numbers (4, 12.5). Your precision
   is about one second; do not claim more. Every time lies between 0 and the video's duration.
3. Be exhaustive in the first 15 seconds: every cut, every new piece of text, every change in
   sound. After 15 seconds, still log every cut and every change, but a long unchanged shot is
   a single entry.
4. Words are verbatim. Copy speech and on-screen text exactly, including slang, profanity and
   emoji. Keep other languages in their language. If you cannot make words out, write
   [inaudible]. Never guess words.
5. On-screen text is only what is written in the frame and legible. Spoken words are not
   on-screen text: if the video has no captions, log no speech_caption entries. If text is too
   small to read, write the part you can read and [unreadable] for the rest; never fill it in.
   A text entry ends when the text leaves the screen.
6. Each track is complete on its own:
   - shots cover the whole video from 0 to the end, with no gaps and no overlaps;
   - every spoken line is in speech;
   - every piece of text on screen is in on_screen_text, including burned-in captions.
7. Use the listed values. When nothing fits, use "other" and say what it is in the
   description. Never stretch a value to fit.
8. If you are unsure whether something happened (a brief flash of product, a faint sound),
   log it with "certain": false. Do not leave it out, and do not state it as fact.
</rules>

<definitions>
shot.framing
  face_close       a face fills most of the frame
  person_medium    a person from the chest or waist up
  person_wide      a whole person, or people in a scene
  product_close    the product or its pack fills most of the frame
  product_in_hand  the product held in a hand, not filling the frame
  screen           a phone or app screen, including a clip of another video or a podcast
  text_card        text on a plain or blurred background is the main thing on screen
  b_roll           other footage: places, food, activities, stock
  other

shot.transition_in
  none        the first shot
  hard_cut    a cut to a different shot
  jump_cut    the same framing with time skipped
  dissolve    a fade or cross-fade
  graphic     a swipe, zoom, or animated wipe

shot.camera
  static | handheld | moving | zoom

speech.speaker
  on_camera   a person visible on screen is speaking
  voiceover   someone speaking who is not on screen
  clip        someone inside an embedded clip (a podcast, an interview, another video)
  unknown

speech.delivery   compared with how this speaker sounds elsewhere in this video
  steady | faster | slower | louder | quieter

on_screen_text.role
  speech_caption  repeats the words being spoken
  hook_title      a headline over the opening
  claim           a statement about the product, a problem, or a benefit
  review          a customer quote, a rating, or a count of reviews
  offer           a price, discount, sale, bundle, or free shipping
  call_to_action  buy, shop, tap, link, or similar
  brand           the brand or product name on its own
  label           a name, credential, or caption naming what is shown
  other

audio.kind
  music | sound_effect | ambient | silence (no music and no speech)

audio.event
  starts | stops | changes (a new track, or a clear shift in tempo or mood) | louder | quieter
  one_off (a single sound effect)

product.state
  pack_visible  in frame but not the focus
  held          in someone's hand
  close_up      fills most of the frame
  used          being opened, poured, taken, or eaten
  logo_only     only the logo or name is visible, not the product
</definitions>"""

FRAMING = ["face_close", "person_medium", "person_wide", "product_close", "product_in_hand",
           "screen", "text_card", "b_roll", "other"]
TRANSITION = ["none", "hard_cut", "jump_cut", "dissolve", "graphic"]
CAMERA = ["static", "handheld", "moving", "zoom"]
SPEAKER = ["on_camera", "voiceover", "clip", "unknown"]
DELIVERY = ["steady", "faster", "slower", "louder", "quieter"]
TEXT_ROLE = ["speech_caption", "hook_title", "claim", "review", "offer", "call_to_action",
             "brand", "label", "other"]
AUDIO_KIND = ["music", "sound_effect", "ambient", "silence"]
AUDIO_EVENT = ["starts", "stops", "changes", "louder", "quieter", "one_off"]
PRODUCT_STATE = ["pack_visible", "held", "close_up", "used", "logo_only"]


def _obj(props: dict[str, Any]) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": props, "required": list(props)}


_N = {"type": "number"}
_S = {"type": "string"}
_B = {"type": "boolean"}


def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": values}


SCHEMA = _obj({
    "language": _S,
    "burned_in_captions": _B,
    "shots": {"type": "array", "items": _obj({
        "start": _N, "end": _N, "framing": _enum(FRAMING), "transition_in": _enum(TRANSITION),
        "camera": _enum(CAMERA), "face_visible": _B, "looking_at_camera": _B,
        "description": _S, "certain": _B,
    })},
    "speech": {"type": "array", "items": _obj({
        "start": _N, "end": _N, "speaker": _enum(SPEAKER), "delivery": _enum(DELIVERY),
        "text": _S, "certain": _B,
    })},
    "on_screen_text": {"type": "array", "items": _obj({
        "start": _N, "end": _N, "role": _enum(TEXT_ROLE), "text": _S, "certain": _B,
    })},
    "audio": {"type": "array", "items": _obj({
        "t": _N, "kind": _enum(AUDIO_KIND), "event": _enum(AUDIO_EVENT), "description": _S, "certain": _B,
    })},
    "product": {"type": "array", "items": _obj({
        "start": _N, "end": _N, "state": _enum(PRODUCT_STATE), "certain": _B,
    })},
})


def user_text(duration_s: float) -> str:
    return f"Based on the video above, write its log. The video is {duration_s:g} seconds long."


async def call(db: Database, client, model: str, org_id: UUID, footage_id: UUID,
               video: bytes, duration_s: float, attempt_tag: str = "") -> tuple[dict, dict]:
    """One ledgered call, no retries. `attempt_tag` separates deliberate repeat runs (the
    repeatability check) from accidental re-sends in the ledger key."""
    key = f"{footage_id}:{VERSION}{attempt_tag}"
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=16000,
            # Perception, not reasoning: Gemini 3's thinking level, passed through the gateway
            # (probed 2026-09-25; accepted).
            extra_body={"providerOptions": {"google": {"thinkingConfig": {"thinkingLevel": "low"}}}},
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_content([], user_text(duration_s), (video, "video/mp4"))},
            ],
            response_format={"type": "json_schema", "json_schema": {"name": "timeline", "schema": SCHEMA, "strict": True}},
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        out = json.loads(choice.message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = getattr(resp, "usage", None)
    usage = {"input_tokens": getattr(u, "prompt_tokens", 0) or 0,
             "output_tokens": getattr(u, "completion_tokens", 0) or 0}
    await model_calls.finish(db, claim.id, **usage)
    return out, usage


def derived(log: dict, duration_s: float) -> dict[str, Any]:
    """Moments computed from the tracks. Never asked of the model."""
    def first(items, pred, key="start"):
        ts = [x[key] for x in items if pred(x)]
        return min(ts) if ts else None

    shots = sorted(log.get("shots", []), key=lambda s: s["start"])
    cuts = [s["start"] for s in shots[1:] if s["transition_in"] != "none"]
    text = log.get("on_screen_text", [])
    speech = log.get("speech", [])

    def covered(items, pred) -> float:
        return sum(max(0.0, min(x["end"], duration_s) - max(x["start"], 0.0)) for x in items if pred(x))

    return {
        "first_product_s": first(log.get("product", []), lambda p: p["state"] != "logo_only"),
        "first_product_close_s": first(log.get("product", []), lambda p: p["state"] == "close_up"),
        "first_brand_text_s": first(text, lambda t: t["role"] == "brand"),
        "first_offer_s": first(text, lambda t: t["role"] == "offer"),
        "first_cta_s": first(text, lambda t: t["role"] == "call_to_action"),
        "first_music_change_s": first(log.get("audio", []), lambda a: a["kind"] == "music" and a["event"] in ("stops", "changes"), key="t"),
        "cuts_first_3s": sum(c < 3 for c in cuts),
        "cuts_first_15s": sum(c < 15 for c in cuts),
        "cuts_total": len(cuts),
        "face_share": covered(shots, lambda s: s["face_visible"]) / duration_s if duration_s else None,
        "product_share": covered(log.get("product", []), lambda p: p["state"] != "logo_only") / duration_s if duration_s else None,
        "speech_share": covered(speech, lambda s: True) / duration_s if duration_s else None,
    }


def _words(s: str) -> str:
    return " ".join(re.findall(r"\w+", s.lower().replace("'", "").replace("’", "")))


async def check(db: Database, footage_id: UUID, log: dict, duration_s: float) -> list[str]:
    """Problems with the log, against itself and against what the tagger recorded."""
    problems: list[str] = []
    tol = 1.0
    for track in ("shots", "speech", "on_screen_text", "product"):
        for x in log.get(track, []):
            if x["start"] < 0 or x["end"] > duration_s + tol or x["start"] > x["end"]:
                problems.append(f"{track}: bad times {x['start']}-{x['end']} (video {duration_s:g}s)")
    for a in log.get("audio", []):
        if not 0 <= a["t"] <= duration_s + tol:
            problems.append(f"audio: time {a['t']} outside the video")
    shots = sorted(log.get("shots", []), key=lambda s: s["start"])
    if shots:
        if shots[0]["start"] > tol:
            problems.append(f"shots: nothing logged before {shots[0]['start']}s")
        if shots[-1]["end"] < duration_s - tol:
            problems.append(f"shots: nothing logged after {shots[-1]['end']}s")
        for a, b in zip(shots, shots[1:]):
            if b["start"] - a["end"] > tol:
                problems.append(f"shots: gap {a['end']}-{b['start']}s")
            if a["end"] - b["start"] > tol:
                problems.append(f"shots: overlap {b['start']}-{a['end']}s")
    else:
        problems.append("shots: empty")

    run = await db.fetchval(
        "SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1 AND source_tag_run_id IS NOT NULL LIMIT 1",
        footage_id,
    )
    if run:
        tagger_transcript = await db.fetchval(
            "SELECT value FROM tag WHERE tag_run_id=$1 AND dimension='transcript' LIMIT 1", run
        ) or ""
        mine = _words(" ".join(s["text"] for s in sorted(log.get("speech", []), key=lambda s: s["start"])))
        theirs = _words(tagger_transcript)
        if theirs or mine:
            agree = SequenceMatcher(None, mine, theirs, autojunk=False).ratio()
            if agree < 0.85:
                problems.append(f"speech: {agree:.0%} word agreement with the tagger's transcript")
        tagger_text = [r["value"] for r in await db.fetch(
            "SELECT value FROM tag WHERE tag_run_id=$1 AND dimension='overlay_texts'", run)]
        logged = _words(" ".join(t["text"] for t in log.get("on_screen_text", [])))
        missing = [t for t in tagger_text if _words(t) and _words(t) not in logged]
        if missing:
            problems.append(f"on_screen_text: {len(missing)} of {len(tagger_text)} tagger text items not found, e.g. {missing[0][:60]!r}")
        # The other direction: text this log claims that the tagger never saw. Invented
        # captions and small print showed up here in v1.
        seen = _words(" ".join(tagger_text))
        extra = [t for t in log.get("on_screen_text", []) if _words(t["text"]) and _words(t["text"]) not in seen]
        if extra:
            problems.append(f"on_screen_text: {len(extra)} of {len(log.get('on_screen_text', []))} logged items not in the tagger's text, "
                            f"e.g. {extra[0]['text'][:60]!r} at {extra[0]['start']}s")
    first_scene = await db.fetchval(
        "SELECT min(t_start_s) FROM scene WHERE footage_id=$1 AND shows_product", footage_id
    )
    d = derived(log, duration_s)
    if first_scene is not None and d["first_product_s"] is not None and abs(first_scene - d["first_product_s"]) > 3:
        problems.append(f"product: first shown at {d['first_product_s']}s here, {first_scene}s in the scene table")
    return problems
