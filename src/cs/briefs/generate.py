"""Write a variant brief for one parent video: one paid call, then code checks.

Code measures (cs.briefs.facts) and code writes every number on the page: the parent's cost,
the curve, the drop-off, the launch plan. The model's job is the creative part: split the
video into slots, propose edits of the original file, new opening lines and a script for a
new creator, and list what claims review should look at.

Checks drop, never repair:
- a quote said to come from the video must be in its transcript;
- a slot or cut must sit inside the video;
- a percentage or price the model writes must already be in the evidence (no invented offers);
- a pickup line must be short enough to shoot in a few seconds.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database

PURPOSE = "briefs.generate"
VERSION = "brief-v2"

KINDS = {
    "cold_open": ("Batch 1 · no new footage", "Share still watching at 3s"),
    "title": ("Batch 1 · no new footage", "Share still watching at 3s"),
    "cut": ("Batch 1 · no new footage", "Click rate and purchase rate"),
    "offer": ("Batch 1 · no new footage", "Purchase rate"),
    "pickup": ("Batch 2 · short pickup, same creator and setting", "Share still watching at 3s"),
}

SYSTEM = """You are a senior performance creative strategist writing an edit brief for a video editor.
The video is a winning Meta ad. The goal is new variants that keep what works and change one part each,
so every result says which change made the difference.

Rules:
1. Slots: split the video into 4-7 consecutive slots (hook, product name, argument, proof, contrast, offer, end card, whatever fits).
   Use the timed lines you are given. `said` must copy the spoken words exactly as in the transcript, or be empty.
2. Variants. Each changes ONE thing. Kinds:
   - cold_open: move one or two consecutive sentences to the very start; everything else stays in order.
   - title: add an on-screen title over the untouched opening. Put the title text in `text`.
   - cut: remove one or more consecutive sentences that are losing viewers or slowing the ad.
   - offer: add or change the end card offer. Never invent a discount; write [current offer] unless the evidence states one.
   - pickup: a new opening line for the same creator to shoot in 2-3 seconds, in their own voice. Put the line in `text`.
   Give 2-3 cold_open or title variants, 1 cut, at most 1 offer, and 2 pickup variants.
   `change` tells the editor exactly what to do in one or two plain sentences, with approximate times (m:ss).
   `slug` is a short CamelCase name for the variant, e.g. ColdOpenJunk.
   `keep` (cold_open and cut only; empty for the others): the new play order as ranges of the numbered
   sentences, e.g. moving [9] to the front of an 11-sentence video is [{"from":9,"to":9},{"from":0,"to":8},{"from":10,"to":10}].
   Code cuts at the pauses between sentences, so never split a sentence.
   `quotes`: the sentences moved or removed, copied exactly; empty for the others.
3. Script: a new script for a different creator that keeps the parent's structure. 20-35 seconds, lines with m:ss times,
   stage directions separate from spoken words. No new health claims beyond what the parent says.
4. Claims: list what a claims reviewer should check: profanity, named retailers or competitors, numeric or health claims, offers.
5. Never write a number about performance (cost, ROAS, percentages of viewers). Code adds those.
6. `title`: a 2-5 word name for the parent's idea, e.g. "Not a sleep aid"."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
        "slots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "said": {"type": "string"},
                    "shown": {"type": "string"},
                },
                "required": ["name", "start", "end", "said", "shown"],
            },
        },
        "variants": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "slug": {"type": "string"},
                    "change": {"type": "string"},
                    "text": {"type": "string"},
                    "quotes": {"type": "array", "items": {"type": "string"}},
                    "keep": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"from": {"type": "integer"}, "to": {"type": "integer"}},
                            "required": ["from", "to"],
                        },
                    },
                },
                "required": ["kind", "slug", "change", "text", "quotes", "keep"],
            },
        },
        "script": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "note": {"type": "string"},
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "t": {"type": "string"},
                            "direction": {"type": "string"},
                            "text": {"type": "string"},
                        },
                        "required": ["t", "direction", "text"],
                    },
                },
            },
            "required": ["note", "lines"],
        },
        "claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "slots", "variants", "script", "claims"],
}


def clock(s: float) -> str:
    s = max(0, int(round(s)))
    return f"{s // 60}:{s % 60:02d}"


def _in(quote: str, transcript: str) -> bool:
    q = " ".join(re.sub(r"[^a-z0-9' ]+", " ", quote.lower().replace("’", "'")).split())
    t = " ".join(re.sub(r"[^a-z0-9' ]+", " ", transcript.lower().replace("’", "'")).split())
    return bool(q) and q in t


_FIGURE = re.compile(r"\d+\s*%|\$\s*\d[\d,.]*")


def _invented_figures(text: str, evidence: str) -> list[str]:
    ev = evidence.replace(" ", "")
    return [m.group(0) for m in _FIGURE.finditer(text) if m.group(0).replace(" ", "") not in ev]


def keep_problem(kind: str, keep: list[dict], n: int) -> str | None:
    """Why an edit list cannot be rendered as the kind it claims to be, or None."""
    order = []
    for r in keep:
        if not (0 <= r["from"] <= r["to"] < n):
            return "edit list names a sentence the video does not have"
        order += range(r["from"], r["to"] + 1)
    if len(set(order)) != len(order):
        return "edit list plays a sentence twice"
    if kind == "cold_open":
        if sorted(order) != list(range(n)) or order[0] == 0:
            return (
                "a cold open must move a later sentence to the front and keep every other sentence"
            )
    elif kind == "cut":
        if order != sorted(order) or len(order) == n or not order:
            return "a cut must drop sentences and keep the rest in order"
    return None


def edit(v: dict, facts: dict) -> dict | None:
    """The render instructions for a variant: source time ranges in play order, text overlays,
    and seconds to hold the last frame. Cut times come from facts, never from the model."""
    S, dur = facts["sentences"], facts["duration_s"]
    if v["kind"] in ("cold_open", "cut") and S:
        segs, tight = [], []
        for r in v["keep"]:
            a, b = S[r["from"]], S[r["to"]]
            segs.append([a["cut_before"], b["cut_after"]])
            if not a.get("clean_before", True):
                tight.append(a["cut_before"])
            if not b.get("clean_after", True):
                tight.append(b["cut_after"])
        return {"segments": segs, "overlays": [], "hold_end": 0, "tight": tight}
    if v["kind"] == "title" and v["text"]:
        return {
            "segments": [[0, dur]],
            "overlays": [{"text": v["text"], "start": 0, "end": 3}],
            "hold_end": 0,
            "tight": [],
        }
    if v["kind"] == "offer":
        card = [s["start"] for s in facts.get("scenes", []) if s["label"] == "text_card"]
        start = card[-1] if card else max(0, dur - 3)
        return {
            "segments": [[0, dur]],
            "overlays": [{"text": "{offer}", "start": start, "end": dur + 1}],
            "hold_end": 1,
            "tight": [],
        }
    return None


def prompt(facts: dict, focus: dict | None = None, rails: list[dict] | None = None) -> str:
    st = facts.get("stats") or {}
    lines = [
        "EVIDENCE",
        facts["evidence"],
        "",
        "NUMBERED SENTENCES with times:",
        *[
            f"  [{k}] {clock(s['start'])}-{clock(s['end'])} {s['text']}"
            for k, s in enumerate(facts["sentences"])
        ],
        "",
        "SCENES:",
        *[f"  {clock(s['start'])}-{clock(s['end'])} {s['label']}" for s in facts["scenes"]],
    ]
    d = facts.get("steepest_drop")
    if d:
        lines += [
            "",
            f"VIEWERS LEAVE FASTEST between {clock(d['start'])} and {clock(d['end'])}, "
            "much faster than the seconds either side. Consider cutting what is said there.",
        ]
    lines += ["", "END CARD OFFER: " + ("present" if facts.get("end_card_offer") else "none shown")]
    if st:
        lines += [
            "",
            "This video had one of the lowest costs per purchase in the account in its first week, and has since worn out.",
        ]
    if rails:
        lines += [
            "",
            "WHAT ALREADY HOLDS FOR THIS KIND OF VIDEO (keep these true in every variant and the script):",
        ]
        lines += [f"  - {r['title']}: {r['body']}" for r in rails]
    if focus:
        lines += [
            "",
            f"TEST THIS FINDING ({'it held up' if focus.get('strength') == 'holds' else 'a lead, not proven'}), "
            "from comparing videos of the same kind as this one:",
            f"  {focus['title']}. {focus['body']}",
            "At least two variants must apply this finding to this video: add or strengthen what it says works, or cut "
            "what it says hurts. Say in each such variant's `change` how it applies the finding, and name its slug after it. "
            "The script for a new creator must apply it too.",
        ]
    return "\n".join(lines)


def check(out: dict, facts: dict) -> tuple[dict, list[str]]:
    transcript, dur, ev = facts["transcript"], facts["duration_s"], facts["evidence"]
    dropped: list[str] = []
    slots = []
    for s in out.get("slots", []):
        if not (0 <= s["start"] < s["end"] <= dur + 0.5):
            dropped.append(f"slot {s['name']}: times outside the video")
            continue
        if s["said"] and not _in(s["said"], transcript):
            dropped.append(f"slot {s['name']}: spoken words not in the transcript")
            s = {**s, "said": ""}
        slots.append(s)
    variants = []
    for v in out.get("variants", []):
        label = f"{v['kind']} {v['slug']}"
        if v["quotes"] and not all(_in(q, transcript) for q in v["quotes"]):
            dropped.append(f"{label}: quoted line not in the transcript")
            continue
        if v["kind"] in ("cold_open", "cut"):
            why = keep_problem(v["kind"], v.get("keep") or [], len(facts["sentences"]))
            if why:
                dropped.append(f"{label}: {why}")
                continue
        if v["kind"] == "pickup" and len(v["text"].split()) > 15:
            dropped.append(f"{label}: pickup line too long to shoot in a few seconds")
            continue
        bad = _invented_figures(v["change"] + " " + v["text"], ev)
        if bad:
            dropped.append(f"{label}: figures not in the evidence ({', '.join(bad)})")
            continue
        variants.append(v)
    script = out.get("script") or {"note": "", "lines": []}
    bad = _invented_figures(" ".join(x["text"] for x in script["lines"]), ev)
    if bad:
        dropped.append(
            f"script: figures not in the evidence ({', '.join(bad)}), replaced with [current offer]"
        )
        for line in script["lines"]:
            line["text"] = _FIGURE.sub("[current offer]", line["text"])
    return {**out, "slots": slots, "variants": variants, "script": script}, dropped


def _prefix(name: str | None) -> str:
    parts = (name or "Parent").split("_")
    return "_".join(parts[:2]) if len(parts) > 1 else parts[0]


def compose(out: dict, facts: dict, dropped: list[str]) -> dict:
    """The editable brief: model's creative work plus code-written numbers and plan."""
    st = facts.get("stats") or {}
    curve = facts.get("curve") or []
    at3 = next((v for t, v in curve if t == 3), None)
    prefix = _prefix(facts.get("name"))
    counters: dict[str, int] = {}
    batches: dict[str, list] = {}
    for v in out["variants"]:
        letter = {"cold_open": "H", "title": "H", "pickup": "H", "cut": "R", "offer": "R"}[
            v["kind"]
        ]
        counters[letter] = counters.get(letter, 0) + 1
        code = f"{letter}{counters[letter]}"
        batch, read_on = KINDS[v["kind"]]
        change = v["change"]
        if v["kind"] in ("title", "pickup") and v["text"] and v["text"] not in change:
            change = f"{change} “{v['text']}”"
        batches.setdefault(batch, []).append(
            {
                "code": code,
                "kind": v["kind"],
                "name": f"{prefix}-{code}_{v['slug']}",
                "change": change,
                "read_on": read_on,
                "edit": edit(v, facts),
            }
        )
    findings = []
    d = facts.get("steepest_drop")
    if d:
        findings.append(
            f"Viewers leave fastest between {clock(d['start'])} and {clock(d['end'])}: {d['from']:.0%} to {d['to']:.0%} still watching, "
            f"about {d['ratio']:.1f} times the rate of the stretches either side."
        )
    if not facts.get("end_card_offer"):
        findings.append("The end card carries no offer.")
    launch = [
        "Put Batch 1 plus the parent as a fresh upload into one new TESTING ad set. Same day, equal budget per ad. The parent is the control.",
        "After about 5,000 impressions each, compare the openings on the share still watching at 3s"
        + (f". The bar is the parent's {at3:.0%}." if at3 else "."),
        "Judge cost per purchase only for the survivors. Telling apart a 50% gap takes about 100 purchases per ad"
        + (
            f", roughly ${st['cpa_7d'] * 100:,.0f} each at the parent's first-week cost."
            if st.get("cpa_7d")
            else "."
        ),
        "Whatever clears its bar becomes the new parent. Batch 2 and the new-creator script go on top of it.",
    ]
    return {
        "title": out.get("title") or facts.get("name") or "Variant brief",
        "slots": out["slots"],
        "findings": findings,
        "batches": [{"title": k, "variants": v} for k, v in batches.items()],
        "script": {"name": f"{prefix}-S1_NewCreator", **out["script"]},
        "launch": launch,
        "claims": out.get("claims", []),
        "offer": "[Current offer]",
        "dropped": dropped,
    }


async def call(
    db: Database, client, model: str, org_id: UUID, brief_id: UUID, text: str
) -> tuple[dict, dict]:
    key = f"{brief_id}:{VERSION}"
    if await model_calls.attempts_for(db, PURPOSE, key):
        raise RuntimeError(
            "this brief was already sent to the model once; paid calls are not retried"
        )
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=24000,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "brief", "schema": SCHEMA, "strict": True},
            },
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = resp.usage
    usage = {"input_tokens": u.prompt_tokens, "output_tokens": u.completion_tokens}
    await model_calls.finish(db, claim.id, **usage)
    return out, usage
