"""Every new video is made in one Seedance 2.5 generation (at most 30 s, 720p), in the form its kind
needs: a presenter on camera with their own voice and lip sync (talking head); several shots with an
off-screen narrator (voiceover over footage, AI); several shots with room sound only, the words shown
as captions (text only). Kinds carried by an expert, a pair or a chat are never made. The prompt follows ByteDance's Seedance 2.5 prompt guide and tutorial
(docs.byteplus.com/en/docs/ModelArk/2607689 and /2607688, read 2026-09-27) plus what practitioners
report for UGC talking heads:

- the reference mapping comes first ("[Image 1] is the product"), one job per asset;
- the audio rule (voice and room tone only, the music words listed) is stated at the start AND the
  end, because Seedance otherwise tends to add music;
- a one-sentence summary, then a fixed person card and voice card, then contiguous integer-second
  timestamps ("0-4 seconds: ...") with each line as `Her line (emotion), in English: {...}` and never
  repeated elsewhere (repeating words triggers burned-in subtitles);
- about 2 words a second at most, and the last 1.5-2 s silent so the last word is not clipped;
- realism written positively (natural skin texture, normal human eyes, five fingers, chest-up,
  window light from one side): Seedance 2.5 has no negative prompt for visuals;
- never "selfie" or "front camera" (our first test came out with a mirror-reversed label); the
  label is asserted to read left to right, and a check after generation flips the video if not;
- hard words spelled the way they are said ("lie-po-SO-mul"), since "liposomal" came out as
  "lipolonic" in the first test.

The model only fills a structured plan (person, voice, setting, timed beats and lines); code writes
the prompt from the template and enforces every rule above, so a bad plan fails before any spend.
A generated person speaks as a presenter explaining the product, never as a customer reporting
their own results: the FTC's 2024 rule bans testimonials from people who do not exist.
"""

from __future__ import annotations

import math
import re
from typing import Any

MODEL = "bytedance/seedance-2.5"
PRICE_PER_S = 0.233  # 720p, measured: an 8 s generation cost $1.86 on 2026-09-27
MAX_S = 30
MIN_S = 8
WORDS_PER_S = 2.2
END_SILENCE_S = 2
RESOLUTION = "720x1280"
READ_WORDS_PER_S = 2.5  # captions a viewer reads, text-only kinds
# Kind (cluster prefix) -> how it is made. A generated doctor or pharmacist is never made.
MODES = {
    "Talking head": "talking_head",
    "Voiceover over footage": "voiceover",
    "AI": "voiceover",
    "Text only": "text_only",
}

# Said the way it should sound. Seedance reads these as written.
SAY_AS = {
    r"\bliposomal\b": "lie-po-SO-mul",
    r"\bashwagandha\b": "ash-wuh-GAHN-duh",
    r"\bKSM-66\b": "K S M sixty-six",
    r"\bbioavailable\b": "bio-uh-VAIL-uh-bul",
}
# A presenter explains; they do not report their own results with the product.
TESTIMONIAL = re.compile(
    r"\b(I|I've|I'm|I was|my|me)\b[^.?!]{0,40}\b(took|take|taking|tried|slept|sleep|felt|feel|noticed|changed|life|results?|week|night)\b",
    re.I,
)
MOVE = re.compile(r"(,\s*)?\b(gentle |slow |subtle )?(push[- ]in|zoom[- ]?in|zoom)\b", re.I)
# The product is described by the reference image alone; a model's description invents details
# (a plan once gave the pouch a window showing the gummies, which it does not have).
PRODUCT = "a supplement pouch"
SAFE_CAMERA = "Chest-up medium close-up, handheld phone at arm's length, slight natural sway"
CAMERA_BANNED = re.compile(r"\b(selfie|front[- ]?(facing )?camera|mirror(ed)?)\b", re.I)

SYSTEM = f"""You turn a script for a brand's video ad into a plan for ONE continuous vertical talking-head video,
made by a video model with its own voice and lip sync. The finished video is at most {MAX_S} seconds.
The speaker is a presenter who explains the product to camera. They NEVER claim personal results or experience with
it ("I took", "my sleep", "changed my life"): the person is generated, and testimonials from people who do not exist
are not allowed. The script's lines, numbered from 0, are what is said; code puts them in and times them.
You direct the video: who, where, how it is filmed, and what they do while saying each line.
Plan:
- person: age (e.g. "early 30s"), gender ("woman" or "man"), look (hair, clothing, one small detail; ordinary and
  natural, not a model), setting (a real, lived-in room), light (one natural source, e.g. "soft daylight from a window
  on camera-left").
- voice: timbre, register, accent, age, pace, e.g. "warm, mid-register, neutral American English, early 30s, measured".
- summary: one sentence: who, where, doing what, with the product.
- product: leave as "the pouch" (the reference image shows it).
- beats: exactly one per script line, in order, each with line (the line's number), camera (framing and movement:
  chest-up medium close-up, handheld at arm's length, slight natural sway; a gentle push-in in one beat at most),
  action and emotion (one mild word: warm, curious, matter-of-fact, reassuring).
- action: one full sentence starting with "She" or "He": ONE simple hand action while saying the line, e.g. raising
  the pouch beside the face with the label facing the camera. The pouch stays in one hand the whole time; no second
  object, no pointing at small details.
- Code adds a silent 2-second ending. Keep the product visible and the face towards the
  camera throughout; no turning to profile, nothing covering the mouth while speaking.
Use only the facts in the evidence you are given; if something is not in it, leave it out.
Never use the words selfie, front camera or mirror. Do not put words on screen. Do not write numbers, prices or offers
the script does not contain."""

SHOTS_SYSTEM = f"""You turn a script for a brand's video ad into a plan for ONE vertical video of several short shots,
made by a video model in one generation, at most {MAX_S} seconds. The script's lines, numbered from 0, are what is
{{said}}; code puts them in and times them. You direct the shots: one shot per script line.
Nobody in the video speaks to the camera or moves their lips. The words never claim personal results or experience
("I took", "my sleep"): nobody in the video is a real customer.
Plan:
- style: look (the visual style, taken from how the best videos of this kind look, e.g. "natural handheld phone
  footage, warm tones"), setting (the world the shots share, e.g. "a calm apartment at night and the next morning"),
  light (one natural source per scene).
- voice: {{voice}}
- summary: one sentence: what the video shows, with the product.
- product: leave as "the pouch" (the reference image shows it).
- beats: exactly one per script line, in order, each with line (the line's number), camera (shot type and at most ONE
  simple move, e.g. "close-up, slow push-in" or "medium shot, static"), shot (one full sentence: the subject, ONE
  action, the setting; key action first; people may appear, seen from behind, at a distance or as hands, never
  talking; the pouch appears in at least two shots, label towards the camera) and emotion (one mild word for the
  narration, e.g. calm, warm, curious; for text-only, the mood of the shot).
- Leave plain, uncluttered space in the lower third of every shot: captions go there.
- Code adds a short ending shot that holds on the pouch.
Use only the facts in the evidence you are given; if something is not in it, leave it out.
Never use the words selfie, front camera or mirror. Do not put words, captions, logos or labels other than the
pouch's own label in any shot. Do not write numbers, prices or offers the script does not contain."""

SHOTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "style": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"look": {"type": "string"}, "setting": {"type": "string"}, "light": {"type": "string"}},
            "required": ["look", "setting", "light"],
        },
        "voice": {"type": "string", "description": "the narrator's voice; empty for text-only"},
        "summary": {"type": "string"},
        "product": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "line": {"type": "integer", "description": "the script line's number"},
                    "camera": {"type": "string", "description": "shot type and at most one simple move"},
                    "shot": {"type": "string", "description": "one full sentence: subject, one action, setting"},
                    "emotion": {"type": "string", "description": "one mild word"},
                },
                "required": ["line", "camera", "shot", "emotion"],
            },
        },
    },
    "required": ["style", "voice", "summary", "product", "beats"],
}


def system(m: str) -> str:
    if m == "talking_head":
        return SYSTEM
    said = "shown on screen as captions" if m == "text_only" else "said by an off-screen narrator"
    voice = (
        'leave empty (there is no voice)'
        if m == "text_only"
        else 'the narrator: timbre, register, accent, age, pace, e.g. "warm, mid-register, neutral American English, '
        'early 30s, measured"'
    )
    return SHOTS_SYSTEM.replace("{said}", said).replace("{voice}", voice)


def schema(m: str) -> dict:
    return SCHEMA if m == "talking_head" else SHOTS_SCHEMA


SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "person": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "age": {"type": "string"},
                "gender": {"type": "string"},
                "look": {"type": "string"},
                "setting": {"type": "string"},
                "light": {"type": "string"},
            },
            "required": ["age", "gender", "look", "setting", "light"],
        },
        "voice": {"type": "string"},
        "summary": {"type": "string"},
        "product": {"type": "string"},
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "line": {"type": "integer", "description": "the script line's number"},
                    "camera": {"type": "string", "description": "framing and movement, e.g. chest-up medium close-up, handheld at arm's length"},
                    "action": {"type": "string", "description": "a full sentence starting with She or He: what they physically do with the pouch while speaking"},
                    "emotion": {"type": "string", "description": "one mild word for how the line is said, e.g. warm"},
                },
                "required": ["line", "camera", "action", "emotion"],
            },
        },
    },
    "required": ["person", "voice", "summary", "product", "beats"],
}


def mode(cluster: str) -> str | None:
    return next((m for prefix, m in MODES.items() if cluster.startswith(prefix)), None)


def makeable(cluster: str) -> bool:
    return mode(cluster) is not None


def speaks(m: str) -> bool:
    return m != "text_only"


def say_as(line: str) -> str:
    for pat, sub in SAY_AS.items():
        line = re.sub(pat, sub, line, flags=re.I)
    return line


def words(line: str) -> int:
    return len(re.findall(r"[A-Za-z0-9'-]+", line))


class PlanError(ValueError):
    """The plan breaks a rule; nothing is generated."""


TOO_CLOSE_LINE = 0.72  # a planned line this alike to any sentence of a video of the kind is a copy


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", t.lower()).split())


def copied_line(line: str, transcripts: list[str]) -> tuple[float, str] | None:
    """The closest stretch any video of the kind already says, when the line is too alike. The
    line is compared word by word with every same-length stretch of each transcript (±2 words),
    so a copied hook inside a long sentence is still caught."""
    from difflib import SequenceMatcher

    a = _norm(line).split()
    if len(a) < 4:
        return None
    best = (0.0, "")
    for t in transcripts:
        w = _norm(t).split()
        for n in range(max(1, len(a) - 2), len(a) + 3):
            for i in range(0, max(1, len(w) - n + 1)):
                r = SequenceMatcher(None, a, w[i : i + n], autojunk=False).ratio()
                if r > best[0]:
                    best = (r, " ".join(w[i : i + n]))
    return best if best[0] >= TOO_CLOSE_LINE else None


def _sentence(text: str, subject: str | None = None) -> str:
    t = text.strip().rstrip(".")
    if not t:
        return ""
    if subject and not re.match(r"(?i)^(she|he|they|her|his)\b", t):
        t = f"{subject} {t[0].lower()}{t[1:]}"
    return t[0].upper() + t[1:] + "."


def check(
    plan: dict,
    evidence: str,
    transcripts: list[str] | None = None,
    script_lines: list[str] | None = None,
    m: str = "talking_head",
) -> dict:
    """Enforce the rules on the model's plan. Raises PlanError before any spend; returns the
    plan with lines respelled for speech and the problems it fixed on the way."""
    from cs.briefs.generate import _invented_figures

    fixed: list[str] = []
    if script_lines is None:
        raise PlanError("no script to say")
    order = [b["line"] for b in plan["beats"]]
    if order != list(range(len(script_lines))):
        raise PlanError(f"the plan must take every script line once, in order; it took {order}")
    # The words are the script's, set by code; each line gets the time it needs at a natural pace.
    beats, t = [], 0
    for b in plan["beats"]:
        line = script_lines[b["line"]]
        # Spoken lines at a natural pace; captions long enough to read, plus a beat to take in the shot.
        need = words(line) / WORDS_PER_S if speaks(m) else words(line) / READ_WORDS_PER_S + 1
        span = max(3, math.ceil(need))
        action = b.get("action") or b.get("shot") or ""
        beats.append({**b, "action": action, "start": t, "end": t + span, "line": line})
        t += span
    # Silence at the end, so the last word is not clipped.
    last = beats[-1]
    if last["line"].strip():
        ending = (
            "Smiles and holds still, looking into the lens."
            if m == "talking_head"
            else "The pouch stands still in sharp focus, label towards the camera."
        )
        beats.append({"start": last["end"], "end": last["end"] + END_SILENCE_S, "camera": last["camera"],
                      "action": ending, "line": "", "emotion": "calm"})
        fixed.append("added a quiet 2-second ending so the last word is not cut off")
    total = beats[-1]["end"]
    if total > MAX_S:
        raise PlanError(f"the plan runs {total} seconds; the limit is {MAX_S}")
    if total < MIN_S:
        raise PlanError(f"the plan runs {total} seconds; at least {MIN_S} are needed")
    for b in beats:
        if b["line"].strip() and len(b["action"].split()) < 5:
            raise PlanError(f"the {'action' if m == 'talking_head' else 'shot'} for {b['start']}-{b['end']} s is not a sentence of what happens: “{b['action']}”")
        if TESTIMONIAL.search(b["line"]):
            raise PlanError(f"{b['start']}-{b['end']} s reads as a personal testimonial: “{b['line']}”")
        near = copied_line(b["line"], transcripts or []) if b["line"].strip() else None
        if near:
            raise PlanError(f"{b['start']}-{b['end']} s repeats a line from a video of this kind ({round(near[0] * 100)}% alike): “{near[1]}”")
        bad = _invented_figures(b["line"], evidence)
        if bad:
            raise PlanError(f"{b['start']}-{b['end']} s uses figures the source videos do not: {', '.join(bad)}")
        for k in ("camera", "action"):
            if CAMERA_BANNED.search(b[k]):
                b[k] = SAFE_CAMERA if k == "camera" else CAMERA_BANNED.sub("", b[k]).strip()
                fixed.append(f"replaced selfie or mirror wording at {b['start']} s")
        b["said_as"] = say_as(b["line"])
    # One camera idea: a push-in or zoom once, not in every beat (a 30 s creep reads as fake).
    seen_move = False
    for b in beats:
        if MOVE.search(b["camera"]):
            if seen_move:
                b["camera"] = re.sub(r",?\s*,", ",", MOVE.sub("", b["camera"])).strip(" ,")
            seen_move = True
    return {**plan, "mode": m, "beats": beats, "duration": total, "fixed": fixed}


NO_MUSIC = "No music, no background music, no BGM, no score, no instrumental, no melody. No subtitles."
PRODUCT_LINE = (
    f"[Image 1] is the product, {PRODUCT}. Reproduce its shape, colours and front label exactly as in [Image 1]; the "
    "label text reads left to right, never mirrored."
)


def prompt(plan: dict) -> str:
    """The Seedance prompt, in the order the official guide gives."""
    m = plan.get("mode", "talking_head")
    if m != "talking_head":
        return shots_prompt(plan, m)
    p = plan["person"]
    female = p["gender"].lower().startswith(("w", "f"))
    she, her = ("Her", "her") if female else ("His", "his")
    audio = f"Audio: only the presenter's voice and quiet room tone. {NO_MUSIC}"
    lines = [
        audio,
        PRODUCT_LINE,
        f"Summary: {plan['summary']} Vertical 9:16 phone video, one continuous take.",
        f"The presenter: a {p['gender']} in {her} {p['age']}, {p['look']}. Setting: {p['setting']}. Voice: {plan['voice']}.",
    ]
    subject = "She" if female else "He"
    for b in plan["beats"]:
        head = f"{b['start']}-{b['end']} seconds: {_sentence(b['camera'])} {_sentence(b['action'], subject)}"
        if b["line"].strip():
            lines.append(f"{head} {she} line ({b['emotion']}), in English: {{{b['said_as']}}}")
        else:
            lines.append(f"{head} {she} lips stay closed. Silent.")
    lines.append(
        f"Additional notes: {p['light']}; natural skin texture with visible pores; normal human eyes; five fingers on "
        f"each hand; chest-up framing, facing the camera; the product stays visible with its label readable; the same "
        f"setting throughout; one continuous take. {audio}"
    )
    return "\n".join(lines)


def shots_prompt(plan: dict, m: str) -> str:
    """Several shots in one generation (ByteDance: "Shot N" with timestamps, one camera idea each)."""
    st = plan["style"]
    if m == "text_only":
        audio = f"Audio: only quiet natural ambient sound for each shot. No voice, no speech, no narration. {NO_MUSIC}"
        voice_line = "Nobody in the video speaks."
    else:
        audio = f"Audio: only an off-screen narrator's voice-over and quiet natural ambience. {NO_MUSIC}"
        voice_line = (
            f"The narrator is off-screen and never seen. Voice: {plan['voice']}. Nobody on screen speaks or moves "
            "their lips."
        )
    lines = [
        audio,
        PRODUCT_LINE,
        f"Summary: {plan['summary']} Vertical 9:16 video of short shots joined by clean hard cuts. Look: {st['look']}. "
        f"Setting: {st['setting']}.",
        voice_line,
    ]
    for n, b in enumerate(plan["beats"], 1):
        head = f"{b['start']}-{b['end']} seconds: Shot {n}. {_sentence(b['camera'])} {_sentence(b['action'])}"
        if b["line"].strip() and m != "text_only":
            lines.append(f"{head} Narrator voice-over ({b['emotion']}), in English: {{{b['said_as']}}}")
        else:
            lines.append(head)
    lines.append(
        f"Additional notes: {st['light']}; natural textures; wherever people appear, normal human eyes and five "
        "fingers on each hand; the pouch keeps its label readable whenever it is shown; plain, uncluttered lower third "
        f"in every shot; no words anywhere except the pouch's own label. {audio}"
    )
    return "\n".join(lines)


def cost(plan: dict) -> dict:
    return {"shots": 1, "seconds": plan["duration"], "usd": round(plan["duration"] * PRICE_PER_S, 2),
            "video_seconds": plan["duration"]}


MIRROR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "text_a": {"type": "string", "description": "the largest words on the package in image A, as they appear"},
        "text_b": {"type": "string", "description": "the largest words on the package in image B, as they appear"},
        "reads_normally": {"type": "string", "enum": ["A", "B"]},
    },
    "required": ["text_a", "text_b", "reads_normally"],
}
