"""Turn the reviewed cluster findings into a playbook organised by creative choice.

What works used to list findings one by one. A marketer decides by choice ("urgency in the
close or not?", "offer card at the end?"), and three findings about the same choice pointed
three ways without saying so. One model call gives every finding a shared choice, a direction
and a scope, and says which videos of its kind carry the trait. Code decides everything else:

- a finding is a **rule** only if it held up in review and has at least MIN_SCRIPTS scripts on
  each side of its comparison; other patterns are **worth testing**;
- a choice is **mixed** when its patterns point opposite ways (in different kinds of video, or
  within one);
- a finding about one script is a **story about that video**, not advice;
- every video code the model names must exist in that cluster, or it is dropped.

Stored as experiment_result key 'playbook' on the tag-clusters experiment.
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from cs.briefs.findings import finding_id
from cs.core import model_calls
from cs.core.db import Database

PURPOSE = "experiments.playbook"
VERSION = "playbook-v1"
SLUG = "tag-clusters"
MIN_SCRIPTS = 3
ELEMENTS = ["opening", "story", "argument", "proof", "offer", "close", "tone", "format"]

SYSTEM = """You organise findings about a brand's video ads into a playbook of creative choices.
Each finding compares the best and worst videos of one kind (a cluster). For each finding:
1. choice_key: the creative choice it is about, as a short snake_case key. Use ONE key for findings about the
   same choice even when they come from different clusters or point different ways (e.g. every finding about an
   end card with an offer or guarantee shares one key). Name each choice once in `choices`.
2. direction: "do" if the finding says having the trait helps the result it measured, "avoid" if it hurts.
3. scope: "pattern" if it compares groups of scripts that share a trait; "one_video" if it is really about one
   specific script, story or series (e.g. "the podcast-reaction story", "the pharmacist's sister story").
4. trait: one plain line a person can check in any video: what to look for.
5. videos_with / videos_without: from that finding's cluster only, the video codes that clearly have / clearly lack
   the trait, judged from their transcripts and end-card text. Leave a video out when you cannot tell.
Choices: name is a short noun phrase a marketer would recognise ("Urgency in the close"); element is one of
opening, story, argument, proof, offer, close, tone, format; question is how a marketer would ask it
("Should the close push urgency?").
Return exactly one entry in `findings` for EVERY finding id given, in the order given. Do not skip any."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "choices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "key": {"type": "string"},
                    "name": {"type": "string"},
                    "element": {"type": "string", "enum": ELEMENTS},
                    "question": {"type": "string"},
                },
                "required": ["key", "name", "element", "question"],
            },
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "choice_key": {"type": "string"},
                    "direction": {"type": "string", "enum": ["do", "avoid"]},
                    "scope": {"type": "string", "enum": ["pattern", "one_video"]},
                    "trait": {"type": "string"},
                    "videos_with": {"type": "array", "items": {"type": "string"}},
                    "videos_without": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "id",
                    "choice_key",
                    "direction",
                    "scope",
                    "trait",
                    "videos_with",
                    "videos_without",
                ],
            },
        },
    },
    "required": ["choices", "findings"],
}


def scripts_per_side(numbers: list[dict]) -> tuple[int, int] | None:
    """(with, without) from a "Scripts: 3 vs 7" style row, or None when the finding has none."""
    for n in numbers:
        if re.search(r"\bscripts?\b", n.get("label", ""), re.I):
            m = re.match(r"\s*(\d+)\s*vs\.?\s*(\d+)", n.get("value", ""))
            if m:
                return int(m.group(1)), int(m.group(2))
    return None


async def inputs(
    db: Database, org_id: UUID, only: set[str] | None = None, existing: dict | None = None
) -> tuple[UUID, dict[str, dict], str]:
    """The experiment, every reviewed finding keyed by id (with its cluster and the cluster's
    videos), and the model's evidence text."""
    eid = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org_id, SLUG)
    reads = await db.fetch(
        "SELECT key, payload->'reviewed' AS reviewed, payload->'videos' AS videos FROM experiment_result"
        " WHERE experiment_id=$1 AND key LIKE 'profile_read:%'",
        eid,
    )
    findings: dict[str, dict] = {}
    blocks: list[str] = []
    for r in reads:
        cluster = r["key"].split(":", 1)[1]
        videos = r["videos"] or {}
        if not r["reviewed"]:
            continue
        ids = {finding_id(cluster, f["title"]) for f in r["reviewed"]}
        for f in r["reviewed"]:
            fid = finding_id(cluster, f["title"])
            findings[fid] = {**f, "id": fid, "cluster": cluster, "cluster_videos": videos}
        if only is not None and not ids & only:
            continue
        lines = [f"CLUSTER: {cluster}", "VIDEOS:"]
        for code, v in sorted(videos.items()):
            ev = await _video_text(db, UUID(v["id"]))
            lines.append(f"  {code} ({v.get('side')}, ${v.get('cpa') or 0:.0f} CPA): {ev}")
        lines.append("FINDINGS:")
        for f in r["reviewed"]:
            fid = finding_id(cluster, f["title"])
            if only is not None and fid not in only:
                continue
            nums = "; ".join(f"{n['label']}: {n['value']}" for n in f.get("numbers") or [])
            lines.append(
                f"  [{fid}] ({f.get('strength')}) {f['title']}. {f['body']} Numbers: {nums}"
            )
        blocks.append("\n".join(lines))
    text = "\n\n".join(blocks)
    if existing:
        named = "\n".join(f"  {k}: {c['name']} ({c['element']})" for k, c in existing.items())
        text = f"CHOICES ALREADY NAMED (reuse these keys where they fit; name any new one in `choices`):\n{named}\n\n{text}"
    return eid, findings, text


async def _video_text(db: Database, footage_id: UUID) -> str:
    rows = await db.fetch(
        """
        SELECT t.dimension, t.value, t.t_start_s FROM tag t
        WHERE t.tag_run_id = (SELECT source_tag_run_id FROM footage_attribute WHERE footage_id=$1
                              AND source_tag_run_id IS NOT NULL LIMIT 1)
          AND t.dimension IN ('transcript', 'overlay_texts')
        ORDER BY t.t_start_s NULLS FIRST, t.ordinal
        """,
        footage_id,
    )
    transcript = next((r["value"] for r in rows if r["dimension"] == "transcript"), "") or ""
    overlays = [r["value"] for r in rows if r["dimension"] == "overlay_texts"][-4:]
    t = re.sub(r"\s+", " ", transcript)
    text = t if len(t) <= 900 else f"{t[:500]} … {t[-380:]}"
    return f'"{text}"' + (f" End screen text: {' / '.join(overlays)}" if overlays else "")


def build(out: dict, findings: dict[str, dict]) -> dict:
    """Checks and tiers. Returns the stored playbook."""
    choices = {c["key"]: c for c in out.get("choices", [])}
    dropped: list[str] = []
    placed: dict[str, dict] = {}
    for m in out.get("findings", []):
        f = findings.get(m["id"])
        if f is None:
            dropped.append(f"unknown finding id {m['id']}")
            continue
        if m["choice_key"] not in choices:
            dropped.append(f"{m['id']}: choice {m['choice_key']} not named")
            continue
        codes = set(f["cluster_videos"])
        bad = [c for c in m["videos_with"] + m["videos_without"] if c not in codes]
        if bad:
            dropped.append(f"{m['id']}: videos not in its cluster {bad}")
        sides = scripts_per_side(f.get("numbers") or [])
        scope = m["scope"]
        if scope == "pattern" and sides and min(sides) < 2:
            scope = "one_video"
        tier = (
            "story"
            if scope == "one_video"
            else "context"
            if f.get("strength") == "context"
            else "rule"
            if f.get("strength") == "holds" and sides and min(sides) >= MIN_SCRIPTS
            else "test"
        )
        placed[m["id"]] = {
            "choice_key": m["choice_key"],
            "direction": m["direction"],
            "scope": scope,
            "tier": tier,
            "trait": m["trait"],
            "scripts": list(sides) if sides else None,
            "videos_with": [c for c in m["videos_with"] if c in codes],
            "videos_without": [c for c in m["videos_without"] if c in codes],
        }
    missing = [fid for fid in findings if fid not in placed]
    dropped += [f"{fid}: not placed by the model" for fid in missing]
    # A choice is mixed when its advice-grade findings point opposite ways.
    verdicts: dict[str, str] = {}
    for key in choices:
        dirs = {
            p["direction"]
            for p in placed.values()
            if p["choice_key"] == key and p["tier"] in ("rule", "test")
        }
        verdicts[key] = "mixed" if len(dirs) > 1 else next(iter(dirs), "none")
    return {
        "version": VERSION,
        "choices": choices,
        "findings": placed,
        "verdicts": verdicts,
        "dropped": dropped,
    }


async def call(
    db: Database, client, model: str, org_id: UUID, key: str, text: str
) -> tuple[dict, dict]:
    if await model_calls.attempts_for(db, PURPOSE, key):
        raise RuntimeError("already sent once; paid calls are not re-sent")
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=24000,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "playbook", "schema": SCHEMA, "strict": True},
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
