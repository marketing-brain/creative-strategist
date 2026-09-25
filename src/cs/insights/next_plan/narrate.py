"""The three model calls behind a recut card: describe the clip, write the card, try to kill it.

Division of labour, unchanged from the 2026-09-22 ruling: **the model is never the source of a
claim.** Statistics decide which file, which second and how much money. The model is allowed to
say what is visible in three seconds of video, and to phrase what the statistics found. It is
not allowed to produce a number, and `render` enforces that mechanically rather than by asking
-- the writer emits placeholders, and any digit in its output is a rejection, not a warning.

Frugality. Steady-state cost is close to zero, by construction:
  - a clip description is keyed by the segment and never expires, because the pixels do not
    change, and reference files recur across cards, so the cache hit rate rises with use;
  - card prose is keyed by a hash of the *rounded* measurements, so a file whose hold drifts a
    tenth of a point reuses yesterday's sentence;
  - only the cards that will actually be shown are narrated -- there is no point paying for
    prose on the eleventh-best card nobody sees.
On org 3 that is 3 clips and 2 text calls on the first night and, for an unchanged account,
nothing at all on the second.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id

log = logging.getLogger("cs.next_plan.narrate")

MAX_CLIPS_PER_RUN = 6  # a cold start on a new account, bounded

DESCRIBE_SYSTEM = """You describe short video clips for a production team that is about to \
reshoot one. Report only what is on screen and what is said. Do not judge quality, do not \
guess at performance, do not speculate about why anything was done. If the clip is silent, \
say so. Be concrete: name what is held, worn, shown, and where the camera is."""

DESCRIBE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "on_screen": {"type": "string", "description": "What is visible, in one sentence."},
        "first_words": {
            "type": "string",
            "description": "The first words spoken, verbatim. Empty if silent.",
        },
        "camera": {"type": "string", "description": "Shot type and framing, in a few words."},
    },
    "required": ["on_screen", "first_words", "camera"],
}

WRITE_SYSTEM = """You write one instruction card for a performance marketer who is going to \
brief an editor tomorrow morning. They are not an analyst. They want to know what to change, \
on which file, and what to leave alone.

You are given measurements that are already final, and literal descriptions of the footage. \
Your job is wording, not analysis. Obey these rules exactly:

1. NEVER write a digit. Every number is supplied as a placeholder in curly braces. Use the \
placeholder verbatim. A digit anywhere in your output is a failure. This includes timecodes \
("0:00", "3s"), counts ("52 files") and percentages -- all of them already exist as \
placeholders, and a phrase handed to you in a placeholder must be used as the placeholder \
rather than retyped.
2. Use only the placeholders listed. Do not invent one.
3. People still watching IS the measured quantity, so you may say the change is meant to keep \
more of them. Do NOT claim it will improve sales, revenue, ROAS, purchases or cost per \
purchase -- none of those were measured for this card.
4. Say the cause is likely, never certain. It is an observational contrast.
5. The instruction must be something an editor can physically do to this file.
6. Plain words. No marketing voice, no exclamation, no "unlock" or "supercharge".
7. The prescribed change is given to you in FACTS. Instruct it. Do not propose a different
   change, and do not argue for this one.
7a. Never name the file in ANY field. The card already shows it, twice. Write "this ad" or
   "the cut", never {ASSET}.
8. The finding is this account's own measurement, quoted under its P-id. Name the P-id once so
   the reader can find it. Never restate the finding as a general truth about advertising.
9. If FACTS gives a second, it is context about where watching drops on this file, not the
   instruction and not a deadline. Do not turn it into one."""

WRITE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {
            "type": "string",
            "description": (
                "The instruction alone, imperative, at most eight words, in plain English. "
                "Do NOT name the file -- the card already shows it. Do NOT use {ATTRIBUTE} or "
                "{CHANGE_TO} here: they are database expressions. Use {TARGET}, which is the "
                "same thing in plain words, or translate it yourself. "
                "Good: 'Cut the all-in-one claim'. 'Slow the cut rate'. 'Brief a shorter cut'. "
                "Bad: 'Create a new cut for duration s is low (under 51.4s)'."
            ),
        },
        "bleed": {
            "type": "string",
            "description": (
                "What is wrong with this file and what it costs. State which side of the "
                "attribute it is on and the measured difference, using {METRIC}, {SIDE_VALUE}, "
                "{OTHER_VALUE}, {N_SIDE}, {N_OTHER}, {PERCENT} and {SPEND}. Cite {PID} once."
            ),
        },
        "keep": {
            "type": "string",
            "description": (
                "What must not change. For an edit, say the rest of the cut stays as it is. "
                "Empty string when FACTS says this needs a new cut rather than an edit."
            ),
        },
        "cause": {
            "type": "string",
            "description": (
                "Why the finding might hold, taken from the read's own hypothesis in FACTS. "
                "It must stay a hypothesis: keep a hedge ('likely', 'may', 'one explanation "
                "is') and never assert the mechanism as established. FACTS hedges it; do not "
                "strip that out. Empty string if FACTS offers none, and never invent one."
            ),
        },
        "copy_this": {
            "type": "string",
            "description": (
                "The files to copy: name them with {REF1} / {REF2}, give their value on the "
                "metric with {REF1_VALUE} / {REF2_VALUE} when supplied, and say what their "
                "footage actually does if WHAT THE FOOTAGE SHOWS describes it. Concrete, not "
                "generalised into advice. Empty string if there are no references."
            ),
        },
        "job": {
            "type": "string",
            "description": (
                "The concrete job, one line, in the words of whoever does it -- and it must "
                "add something the title does not already say. For an edit, what to look for "
                "in this ad and what to do with it. For a new cut, what to brief. Action "
                "only: no reason, no benefit, no 'so that' or 'to keep' clause. Never restate "
                "the title. {ATTRIBUTE} and {CHANGE_TO} are rejected here: use {TARGET} or "
                "plain words."
            ),
        },
    },
    "required": ["title", "bleed", "keep", "cause", "copy_this", "job"],
}

CRITIC_SYSTEM = """You are checking a recommendation card before it is shown to a customer. \
You are not its author and you are not being helpful to it. Answer only from FACTS.

DO NOT CHECK ARITHMETIC OR NUMBERS, and do not compare one figure to another. Every figure in \
the card was substituted mechanically from FACTS after the card was written; the author was \
physically unable to type a digit, and differences are computed from the percentages shown. \
A group average and this file's own value are different quantities and disagreeing is normal. \
Numbers are not your job. Judge the words.

Answer each question independently. Say true only if you are confident."""

CRITIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "promises_unmeasured_outcome": {
            "type": "boolean",
            "description": (
                "True if the card promises revenue, profit, ROAS, purchases, cost per "
                "purchase, or any result that FACTS did not measure. Viewers still watching "
                "IS measured; the money figure is media already spent and is fine to quote."
            ),
        },
        "states_cause_as_certain": {
            "type": "boolean",
            "description": "True if the card presents the cause as proven rather than likely.",
        },
        "instruction_not_doable": {
            "type": "boolean",
            "description": (
                "True only if an editor could not physically carry out the instruction on this "
                "file, or if it is a different edit from the one FACTS prescribes. Naming what "
                "to shoot, what to show and what to say in the replacement is expected and "
                "wanted -- that specificity is the point of the card, not a deviation."
            ),
        },
        "contradicts_facts": {
            "type": "boolean",
            "description": (
                "True if a statement in words contradicts FACTS. Ignore numeric differences."
            ),
        },
        "problems": {
            "type": "array",
            "items": {"type": "string"},
            "description": "One short line per question answered true. Empty if none.",
        },
    },
    "required": [
        "promises_unmeasured_outcome",
        "states_cause_as_certain",
        "instruction_not_doable",
        "contradicts_facts",
        "problems",
    ],
}

# The verdict is computed here, not asked for. A free-form "kill or pass" invited the model to
# invent a generic objection and then honour it; four narrow questions it answers independently
# leave nothing to summarise.
CRITIC_CHECKS = (
    "promises_unmeasured_outcome",
    "states_cause_as_certain",
    "instruction_not_doable",
    "contradicts_facts",
)


def _prompt_version(parts: list) -> str:
    """The cache key's version, derived from the prompts rather than typed by hand.

    A hand-bumped constant is a bug waiting for the day someone edits a prompt and forgets --
    which happened the first time these prompts were tuned: three rounds of edits all served
    the same cached refusal, and the ledger showed no calls at all. Hashing the text means an
    edit invalidates exactly what it should and nothing else.
    """
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:12]


# Versioned per kind, not globally: tuning the writer should not make the account re-pay for
# every clip description it already has. The first version of this hashed all six together and
# a one-word edit to the writer's rules threw away seven cached descriptions.
PROMPT_VERSION = {
    "clip": _prompt_version([DESCRIBE_SYSTEM, DESCRIBE_SCHEMA]),
    "card": _prompt_version([WRITE_SYSTEM, WRITE_SCHEMA, CRITIC_SYSTEM, CRITIC_SCHEMA]),
}

_DIGIT = re.compile(r"\d")
_DIGIT_RUN = re.compile(r"\d+")

# Placeholders that carry a taxonomy expression. Useful inside a sentence that explains the
# evidence; fatal in a title, which is the one line the reader is certain to read. Asking the
# writer nicely produced "Create a new cut for duration s is low (under 51.4s)", so the rule
# is enforced here instead.
_JARGON_TOKENS = ("ATTRIBUTE", "CHANGE_TO")
_JARGON_FIELDS = ("title", "job")
_TOKEN = re.compile(r"\{([A-Z_0-9]+)\}")


class Rejected(RuntimeError):
    """The writer broke a rule that is checked in code, not by another model."""


@dataclass
class Narration:
    title: str
    bleed: str
    keep: str
    cause: str
    copy_this: str
    job: str
    verdict: str
    problems: list[str]
    cached: bool


@dataclass
class Refusal:
    """Why a card kept its template wording. Never silent: a card that lost its prose to a
    mechanical rule and one the critic killed are different events, and only one of them is
    the system working as designed."""

    stage: str  # "write" | "render" | "critic"
    detail: str


def render(fields: dict[str, str], values: dict[str, str], quotable: str = "") -> dict[str, str]:
    """Substitute the placeholders, refusing anything the writer made up.

    Two rules, both mechanical. No digit may appear in the raw output -- that is what stops a
    number being invented, and it is why even the file's own name arrives as a placeholder
    (`MagAshwa_v26_...` is full of digits). No placeholder may be used that was not supplied --
    that is what stops a plausible-looking `{CPA}` appearing in a card that never measured one.

    `quotable` is the one exception, and it is a narrow one. Inventing a number and repeating
    one we handed over are different acts: the first card asked to quote its reference footage
    came back with an editor's own on-screen line, "me bc I'm 34 and...", and a rule that
    cannot tell that from a fabricated hold rate would forbid quoting real ad copy at all.
    A digit run is allowed only if that exact run appears in the source text the writer was
    given. Everything else is still a rejection.
    """
    # A digit run is fabricated only if it appears nowhere it was given. Three sources count:
    # the placeholder values themselves (the writer typing "51.4s" instead of "{TARGET}" has
    # copied a supplied number, not invented one), the source footage text it was asked to
    # quote, and nothing else. Whether a real number is used in the *right place* is the
    # critic's job; this gate exists to stop numbers appearing from nowhere.
    allowed = set(_DIGIT_RUN.findall(quotable))
    allowed |= {run for v in values.values() for run in _DIGIT_RUN.findall(str(v))}
    out: dict[str, str] = {}
    for key, text in fields.items():
        unknown = {t for t in _TOKEN.findall(text) if t not in values}
        if unknown:
            raise Rejected(f"{key}: unknown placeholder(s) {sorted(unknown)}")
        # Placeholder names carry digits of their own ({REF1}, {REF2}), so the scan runs on
        # the text with the tokens taken out -- otherwise naming a reference is a rejection.
        if key in _JARGON_FIELDS:
            used = set(_TOKEN.findall(text))
            if jargon := used.intersection(_JARGON_TOKENS):
                raise Rejected(
                    f"{key}: used the taxonomy expression {sorted(jargon)} where plain words "
                    f"are needed: {text!r}"
                )
        bare = _TOKEN.sub(" ", text)
        for hit in _DIGIT_RUN.finditer(bare):
            if hit.group() in allowed:
                continue
            # Point at the digit, not at the whole field: a 300-character truncation of the
            # card is not something anyone can act on.
            lo, hi = max(0, hit.start() - 40), min(len(bare), hit.end() + 40)
            raise Rejected(f"{key}: wrote the number {hit.group()!r} here: ...{bare[lo:hi]}...")
        out[key] = _TOKEN.sub(lambda m: values[m.group(1)], text)
    return out


def cache_key(kind: str, parts: list[str]) -> str:
    raw = "|".join([PROMPT_VERSION.get(kind, "x"), kind, *parts])
    return hashlib.sha256(raw.encode()).hexdigest()


async def cached(db: Database, kind: str, key: str, model: str) -> dict | None:
    row = await db.fetchrow(
        "SELECT payload FROM recut_note WHERE kind=$1 AND cache_key=$2 AND model=$3",
        kind,
        key,
        model,
    )
    if row is None:
        return None
    payload = row["payload"]
    return payload if isinstance(payload, dict) else json.loads(payload)


async def remember(
    db: Database, org_id: UUID, kind: str, key: str, model: str, payload: dict
) -> None:
    await db.execute(
        "INSERT INTO recut_note (id, org_id, kind, cache_key, model, payload) "
        "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (kind, cache_key, model) DO NOTHING",
        new_id(),
        org_id,
        kind,
        key,
        model,
        payload,
    )


def _pct(x: float) -> int:
    return round(x * 100)


def _points(hi: float, lo: float) -> str:
    """A difference computed from the percentages the card actually prints.

    Rounding the two holds and the gap between them independently produces a card whose own
    arithmetic does not close -- "67% against 39%, a 27 point difference" -- and a reader who
    subtracts catches it immediately. (The critic caught exactly this on the first card that
    survived to it.) Every difference shown is therefore derived from the shown values.
    """
    return str(_pct(hi) - _pct(lo))


def facts_for(card) -> tuple[dict[str, str], str]:
    """(placeholder values, the FACTS block both the writer and the critic are held to).

    Built from the account read's own cited test, so the card and `/insights` cannot disagree:
    the numbers here are the numbers that page prints under the same P-id.
    """
    from cs.insights.next_plan.card import _fmt, phrase, plain_value

    e = card.evidence
    v: dict[str, str] = {
        "ASSET": card.title,
        "SPEND": f"${card.spend_30d:,.0f}",
        "PID": e.pid,
        "GRADE": e.grade,
        "FINDING": e.title,
        "ATTRIBUTE": e.label,
        "CHANGE_TO": phrase(e),
        # The same change without the taxonomy scaffolding, for the two fields that must read
        # as English: "under 51.4s", "all in one solution".
        "TARGET": plain_value(e),
        "METRIC": e.metric_name,
        "SIDE_VALUE": _fmt(e.metric, e.cell_value),
        "OTHER_VALUE": _fmt(e.metric, e.comp_value),
        "PERCENT": f"{e.change * 100:+.0f}%",
        "N_SIDE": str(e.n_cell),
        "N_OTHER": str(e.n_complement),
    }
    if card.moment is not None:
        m = card.moment
        v |= {
            "AT": str(m.at_s),
            "HOLD": f"{m.hold:.0%}",
            "CONTROL": f"{m.control_hold:.0%}",
            "CONTROL_N": str(m.control_n),
            "CONTROL_KIND": m.control_kind,
        }
    for i, r in enumerate(card.refs[:2], start=1):
        v[f"REF{i}"] = r.title
        if r.value is not None:
            v[f"REF{i}_VALUE"] = _fmt(e.metric, r.value)

    lines = [
        f"FILE: {v['ASSET']}. It spent {v['SPEND']} in the last 30 days.",
        f"FINDING {v['PID']} (graded {v['GRADE']}), from this account's own read: {v['FINDING']}",
        f"MEASURED: {v['METRIC']} is {v['SIDE_VALUE']} across the {v['N_SIDE']} files that are "
        f"{v['ATTRIBUTE']}, against {v['OTHER_VALUE']} for the {v['N_OTHER']} that are not "
        f"({v['PERCENT']}).",
        f"THIS FILE is on the wrong side of it: it {'is not' if e.add else 'is'} {v['ATTRIBUTE']}.",
        f"THE PRESCRIBED CHANGE, already decided before you were asked: {v['CHANGE_TO']}. "
        + (
            "This cannot be done by editing the existing file — it needs a new cut, so the "
            "card must brief the next one rather than instruct an edit."
            if card.verb == "reshoot"
            else "This is an edit to the existing file; the rest of the cut stays as it is."
        ),
        f"DOWNSTREAM: {card.money_note}",
    ]
    if card.trade:
        lines.append(f"WHAT IT COSTS (the card must say this, not bury it): {card.trade}")
    if e.mechanism:
        lines.append(f"WHY IT MIGHT BE TRUE (the read's own words, a hypothesis): {e.mechanism}")
    if card.moment is not None:
        lines.append(
            f"CONTEXT, not the instruction: on this file the watching drops earliest at "
            f"{v['AT']} seconds, holding {v['HOLD']} against {v['CONTROL']} for "
            f"{v['CONTROL_N']} {v['CONTROL_KIND']}."
        )
    for i, r in enumerate(card.refs[:2], start=1):
        val = f" at {_fmt(e.metric, r.value)} {e.metric_name}" if r.value is not None else ""
        lines.append(
            f"REFERENCE {i}: {r.title} is on the right side of this finding{val}, on "
            f"${r.spend_30d:,.0f} of spend."
        )
    lines.append(
        "Everything above is measured. Nothing here forecasts what the change will earn, and "
        "the card must not claim one."
    )
    return v, "\n".join(lines)


def placeholder_help(values: dict[str, str]) -> str:
    return "\n".join(f"  {{{k}}} -> {v}" for k, v in values.items())


class Narrator:
    """The three calls, each ledgered and each cached.

    No retries: a paid call that fails is left failed and the card falls back to its template
    wording (`bd19f3b`). A recut card is useful without prose; it is not useful with prose
    nobody checked.
    """

    def __init__(self, settings, client=None):
        import openai

        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key,
            base_url=f"{settings.ai_gateway_base_url}/v1",
        )
        self.clip_model = settings.video_tagging_model
        self.write_model = settings.recut_write_model
        self.critic_model = settings.recut_critic_model

    async def _json(self, model, system, user_content, schema, name) -> dict:
        resp = await self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": name, "schema": schema, "strict": True},
            },
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        usage = getattr(resp, "usage", None)
        return {
            "payload": json.loads(choice.message.content or "{}"),
            "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
            "model": resp.model,
        }

    async def describe(
        self, db: Database, org_id: UUID, footage_id: str, video: bytes, t0: float, t1: float
    ) -> dict:
        """What is literally in one segment. Cached on the segment, forever."""
        from cs.core import model_calls
        from cs.tagging.tagger import build_content

        key = cache_key("clip", [footage_id, f"{t0:.1f}", f"{t1:.1f}"])
        if (hit := await cached(db, "clip", key, self.clip_model)) is not None:
            return hit | {"cached": True}
        claim = await model_calls.claim(db, org_id, "next_plan.describe", key, self.clip_model)
        try:
            got = await self._json(
                self.clip_model,
                DESCRIBE_SYSTEM,
                build_content(
                    [],
                    f"Describe this {t1 - t0:.0f}-second clip. It is the opening of a video ad.",
                    (video, "video/mp4"),
                ),
                DESCRIBE_SCHEMA,
                "clip",
            )
        except Exception as exc:
            await model_calls.fail(db, claim.id, str(exc)[:500])
            raise
        await model_calls.finish(
            db, claim.id, input_tokens=got["input_tokens"], output_tokens=got["output_tokens"]
        )
        await remember(db, org_id, "clip", key, self.clip_model, got["payload"])
        return got["payload"] | {"cached": False}

    async def narrate(
        self, db: Database, org_id: UUID, card, clips: dict[str, dict]
    ) -> Narration | Refusal:
        """Write the card, then try to kill it. A `Refusal` means the caller keeps the
        template wording, and says which stage refused it."""
        from cs.core import model_calls

        values, facts = facts_for(card)
        shown = {k: v for k, v in sorted(values.items())}
        key = cache_key(
            "card", [card.key, json.dumps(shown, sort_keys=True), json.dumps(clips, sort_keys=True)]
        )
        if (hit := await cached(db, "card", key, self.write_model)) is not None:
            if hit.get("verdict") == "pass":
                return Narration(**hit, cached=True)
            return Refusal("critic", "; ".join(hit.get("problems") or ["killed (cached)"]))

        footage_notes = "\n".join(
            f"{who}: {n.get('on_screen', '')} Camera: {n.get('camera', '')} "
            f"First words: {n.get('first_words') or '(silent)'}"
            for who, n in clips.items()
        )
        prompt = (
            f"FACTS\n{facts}\n\n"
            f"WHAT THE FOOTAGE SHOWS\n{footage_notes or '(no description available)'}\n\n"
            f"PLACEHOLDERS you may use (left side only, verbatim):\n{placeholder_help(values)}\n\n"
            "Write the card."
        )
        claim = await model_calls.claim(db, org_id, "next_plan.write", key, self.write_model)
        try:
            got = await self._json(self.write_model, WRITE_SYSTEM, prompt, WRITE_SCHEMA, "card")
        except Exception as exc:
            await model_calls.fail(db, claim.id, str(exc)[:500])
            return Refusal("write", str(exc)[:200])
        await model_calls.finish(
            db, claim.id, input_tokens=got["input_tokens"], output_tokens=got["output_tokens"]
        )
        try:
            fields = render(got["payload"], values, quotable=footage_notes)
        except Rejected as exc:
            log.warning("writer rejected", extra={"card": card.key, "why": str(exc)})
            return Refusal("render", str(exc)[:300])

        card_text = "\n".join(f"{k.upper()}: {v}" for k, v in fields.items() if v)
        ckey = cache_key("card", [key, card_text, "critic"])
        cclaim = await model_calls.claim(db, org_id, "next_plan.critic", ckey, self.critic_model)
        try:
            crit = await self._json(
                self.critic_model,
                CRITIC_SYSTEM,
                f"FACTS\n{facts}\n\nCARD\n{card_text}",
                CRITIC_SCHEMA,
                "critique",
            )
        except Exception as exc:
            await model_calls.fail(db, cclaim.id, str(exc)[:500])
            return Refusal("critic", str(exc)[:200])
        await model_calls.finish(
            db, cclaim.id, input_tokens=crit["input_tokens"], output_tokens=crit["output_tokens"]
        )
        answers = crit["payload"]
        failed = [c for c in CRITIC_CHECKS if answers.get(c)]
        verdict = "kill" if failed else "pass"
        problems = list(answers.get("problems") or []) or failed
        payload = fields | {"verdict": verdict, "problems": problems}
        await remember(db, org_id, "card", key, self.write_model, payload)
        if verdict != "pass":
            log.info("critic killed card", extra={"card": card.key, "problems": problems})
            return Refusal("critic", "; ".join(problems) or "killed, no reason given")
        return Narration(**payload, cached=False)
