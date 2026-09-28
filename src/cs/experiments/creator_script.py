"""A script for a new creator, per cluster: the same kind of video (who carries it, when the
product first shows) made by someone new, following what holds up for that kind.

It used to be part of every video's brief, which gave one script per video and none per kind.
Here it is written once per cluster, from the cluster's playbook findings (rules to follow,
shared traits to keep, at most one lead to try, and every 'avoid' finding of any tier as a thing
the script must never do) and the evidence of its best videos: the whole best third of the stored
profile read, in its ranking order (cost per purchase pulled toward the cluster's by a prior, so a
video with 8 purchases does not outrank one with 200 on a few dollars).

The best videos are also watched (Gemini, the video passed by link through the gateway's
OpenResponses route): what text tags miss (pacing, what is on screen during each line, how the
product is handled) comes back as a shot list, kept per video so a rewrite does not pay again.
About $0.006 for a one-minute video (probe 2026-09-27).

One paid call per click for the script, never retried. Code checks the output:
- figures ($, %) not in the source videos are replaced with [current offer];
- a finding the script says it follows must be one it was given, and never one to avoid;
- every finding to avoid must be accounted for (how the script stays clear of it), else listed;
- rules the script does not say it follows are listed as missed, for the page to show;
- the spoken script is compared with every transcript in the cluster: a new creator's script
  must not be a rewording of one that already ran (same-script twins measure 0.7).

Stored as experiment_result key 'creator_script:<cluster>' on the tag-clusters experiment.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

import httpx

from cs.briefs.findings import all_findings, cluster_members
from cs.briefs.generate import _FIGURE, _invented_figures
from cs.core import model_calls
from cs.core.db import Database
from cs.core.ids import new_id
from cs.experiments.describe import video_evidence
from cs.make import compliance
from cs.make import seedance as sd

PURPOSE = "experiments.creator_script"
VERSION = "creator-script-v1"
SLUG = "tag-clusters"
KEY = "creator_script:"
BEST_VIDEOS = 3  # the best third, in rank order; small kinds have 1-3
EVIDENCE_CHARS = 5000  # per video: transcript, on-screen text, scenes
WATCH_PURPOSE = "experiments.watch_video"
WATCH_VERSION = "watch-v1"  # the prompt was refined 2026-09-27; stored watches stay valid
WATCH_KEY = "watch:"
MAX_WORDS = 66  # 30 s at 2.2 words a second
TOO_CLOSE = 0.5  # spoken-text similarity to any video in the cluster that blocks the script
# Google's video guidance: the video part first, the instruction after it; times as MM:SS; keep
# what is said apart from what is seen, so a transcript is not passed off as a description.
WATCH_PROMPT = (
    "You are watching a short vertical video ad. List its shots in order. For each shot give its start time as MM:SS, "
    "then VISUAL: what is on screen, where the person is, what they do with their hands and the product, framing and "
    "camera movement; then SPOKEN: the words said during the shot; then TEXT: any on-screen text. After the list, give "
    "one line each for: when the product first appears (MM:SS); the pace of the edit (cuts per 10 seconds); what the "
    "first 3 seconds do to stop the scroll. Describe only what you can see and hear; if unsure, say so."
)

SYSTEM = """You write a script for a NEW creator to film, for a brand's video ads.
The script must be the same kind of video as the kind named (who carries it on camera, and when the product first
appears): a new person making that kind of video, not a copy of any one video.
You get: the kind; RULES that held up when the best and worst videos of this kind were compared (follow every one);
SHARED traits every video of this kind has (keep them); LEADS that are not proven (you may apply at most one, and
say so); NEVER: things that went with the worse videos of this kind (the script must not do any of them); and the evidence of the best videos of this kind (transcript, on-screen text, scenes, and a shot list from
watching them), for the product's real claims, the voice and how they are filmed.
Make it new: a different person, setting, story and wording. Borrow how the best videos work (pacing, when the
product shows, what the opening does), never their lines, props or setting.
Write:
- title: 2-5 words.
- note: one or two plain sentences for the creator: what this video is and what to keep.
- lines: 15-30 seconds in all when read aloud (about 2 words per second, so at most 55 spoken words). Each line has `t` (m:ss start), `direction` (what we see or the creator does; empty if
  nothing), `text` (the spoken words only; for a kind with no one speaking, the on-screen text or voiceover).
- follows: for each rule, shared trait or lead the script applies, its id and one short line on where and how.
- avoids: for each NEVER item, its id and one short line on how the script stays clear of it.
- claims: what a claims reviewer should check (health or numeric claims, offers, named retailers or competitors).
Never invent a health claim, number, price or offer that the best videos do not make. Write [current offer] for any
offer. Never write performance numbers (cost, ROAS, rates).
Never say the product cures, treats or prevents anything, never name a disease, and never ask the viewer about a
condition ("Struggling with sleep?"): supplement ads may only describe how the product supports the body.
Use only the facts in the evidence you are given; if something is not in it, leave it out."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string"},
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
        "follows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "string"}, "how": {"type": "string"}},
                "required": ["id", "how"],
            },
        },
        "avoids": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"id": {"type": "string"}, "how": {"type": "string"}},
                "required": ["id", "how"],
            },
        },
        "claims": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "note", "lines", "follows", "avoids", "claims"],
}


async def experiment_id(db: Database, org_id: UUID) -> UUID | None:
    return await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org_id, SLUG)


async def stored(db: Database, eid: UUID) -> dict[str, dict]:
    """cluster -> its stored script."""
    return {
        r["key"][len(KEY):]: {**r["payload"], "created_at": r["created_at"].isoformat()}
        for r in await db.fetch(
            "SELECT key, payload, created_at FROM experiment_result WHERE experiment_id=$1 AND key LIKE $2",
            eid,
            KEY + "%",
        )
    }


async def watch(db: Database, settings, org_id: UUID, eid: UUID, footage_id: UUID, video_url: str) -> str | None:
    """A shot list from watching the video, once per video and version. None when the one
    attempt failed (paid calls are not retried; the script then goes on without it)."""
    key = f"{WATCH_KEY}{footage_id}:{WATCH_VERSION}"
    have = await db.fetchval(
        "SELECT payload->>'text' FROM experiment_result WHERE experiment_id=$1 AND key=$2", eid, key
    )
    if have:
        return have
    if await model_calls.attempts_for(db, WATCH_PURPOSE, key):
        return None
    model = settings.brief_model
    claim = await model_calls.claim(db, org_id, WATCH_PURPOSE, key, model)
    try:
        async with httpx.AsyncClient(timeout=300) as c:
            resp = await c.post(
                f"{settings.ai_gateway_base_url}/v1/responses",
                headers={"Authorization": f"Bearer {settings.ai_gateway_api_key}"},
                json={
                    "model": model,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_video", "video_url": video_url},
                                {"type": "input_text", "text": WATCH_PROMPT},
                            ],
                        }
                    ],
                },
            )
        resp.raise_for_status()
        j = resp.json()
    except Exception as exc:  # noqa: BLE001 - recorded; the script proceeds without watching
        await model_calls.fail(db, claim.id, str(exc)[:500])
        return None
    u = j.get("usage") or {}
    await model_calls.finish(
        db, claim.id, input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0)
    )
    text = j.get("output_text") or "".join(
        p.get("text", "")
        for o in j.get("output", [])
        for p in (o.get("content") or [])
        if isinstance(p, dict)
    )
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
        eid,
        key,
        {"text": text, "model": model},
    )
    return text


def _transcript(evidence: str) -> str:
    m = re.search(r"^Transcript: (.*?)(?:\nAd copy shown with it:|\Z)", evidence, re.S | re.M)
    return m.group(1).strip() if m else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9áéíóúñü]+", " ", s.lower()).strip()


async def cluster_transcripts(db: Database, org_id: UUID, cluster: str) -> list[dict]:
    """Every video in the cluster with its transcript, for the closeness check."""
    ids = [f for f, c in (await cluster_members(db, org_id)).items() if c == cluster]
    out = []
    for f in await db.fetch(
        """
        SELECT f.id, f.duration_s, f.rep_creative_id,
               (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name
        FROM footage f WHERE f.id = ANY($1::uuid[])
        """,
        ids,
    ):
        t = _transcript((await video_evidence(db, f)).text)
        if t and t.lower() not in ("(no speech)", "no speech", "none"):
            out.append({"footage_id": str(f["id"]), "name": f["name"], "transcript": t})
    return out


def closest(script_text: str, transcripts: list[dict]) -> dict | None:
    a = _norm(script_text)
    best = None
    for t in transcripts:
        r = SequenceMatcher(None, a, _norm(t["transcript"]), autojunk=False).ratio()
        if best is None or r > best["ratio"]:
            best = {"footage_id": t["footage_id"], "name": t["name"], "ratio": round(r, 3)}
    return best


async def inputs(db: Database, org_id: UUID, eid: UUID, cluster: str, settings=None, blobs=None) -> dict:
    """The cluster's findings by tier (from the playbook) and its best videos' transcripts."""
    pb = await db.fetchval(
        "SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key='playbook'", eid
    )
    placed = (pb or {}).get("findings", {})
    # 'do' findings by tier; every 'avoid' of any tier (story included) is a thing never to do.
    groups: dict[str, list[dict]] = {"rule": [], "context": [], "test": [], "avoid": []}
    for fid, f in (await all_findings(db, org_id)).items():
        p = placed.get(fid)
        if f["cluster"] != cluster or p is None:
            continue
        item = {"id": fid, "title": f["title"], "direction": p["direction"], "trait": p["trait"]}
        if p["direction"] == "avoid":
            groups["avoid"].append(item)
        elif p["tier"] in ("rule", "context", "test"):
            groups[p["tier"]].append(item)
    read = await db.fetchval(
        "SELECT payload->'videos' FROM experiment_result WHERE experiment_id=$1 AND key=$2",
        eid,
        f"profile_read:{cluster}",
    )
    # The read's codes (V01, V02, ...) follow its ranking, best first.
    best = [v for _, v in sorted((read or {}).items()) if v.get("side") == "best" and v.get("cpa")][:BEST_VIDEOS]
    videos = []
    for v in best:
        f = await db.fetchrow(
            "SELECT f.id, f.duration_s, f.rep_creative_id, a.path FROM footage f"
            " LEFT JOIN creative c ON c.id = f.rep_creative_id LEFT JOIN asset a ON a.id = c.primary_asset_id"
            " WHERE f.id=$1",
            UUID(v["id"]),
        )
        if f is None:
            continue
        ev = await video_evidence(db, f)
        watched = None
        if settings is not None and blobs is not None and f["path"]:
            watched = await watch(db, settings, org_id, eid, f["id"], await blobs.url_for(f["path"]))
        videos.append(
            {
                "footage_id": v["id"],
                "name": v.get("name"),
                "cpa": v.get("cpa"),
                "evidence": ev.text[:EVIDENCE_CHARS],
                "watched": watched,
            }
        )
    return {"cluster": cluster, **groups, "videos": videos}


def prompt(x: dict) -> str:
    def block(title: str, items: list[dict]) -> list[str]:
        if not items:
            return [f"{title}: none"]
        return [f"{title}:"] + [
            f"  [{f['id']}] {f['direction'].upper()}: {f['title']}. Look for: {f['trait']}" for f in items
        ]

    lines = [f"KIND OF VIDEO: {x['cluster']}", ""]
    lines += block("RULES (follow every one)", x["rule"]) + [""]
    lines += block("SHARED (keep)", x["context"]) + [""]
    lines += block("LEADS (at most one, optional)", x["test"]) + [""]
    lines += block("NEVER (the script must not do any of these)", x["avoid"]) + [""]
    for i, v in enumerate(x["videos"], 1):
        lines += [f"BEST VIDEO {i}:", v["evidence"], ""]
        if v.get("watched"):
            lines += [f"BEST VIDEO {i}, WATCHED (shot list):", v["watched"], ""]
    return "\n".join(lines)


PRESENTER = f"""

THIS KIND IS MADE BY A VIDEO MODEL, so the script is exactly what will be said (by a presenter on camera or a
narrator) or, for text-only, shown on screen:
- The words explain the product. Never first-person experience or results ("I took", "I switched", "my sleep",
  "I'm waking up rested"): nobody in the video is a real customer, and testimonials from people who do not exist are
  not allowed.
- At most {sd.MAX_S - sd.END_SILENCE_S} seconds spoken: {int((sd.MAX_S - sd.END_SILENCE_S) * 2)} words in all at most, lines of 6-14 words.
- Never reuse a sentence or hook from the best videos; say the same argument in new words.
- direction: for a presenter, one simple action with the product in one hand; otherwise what the shot shows.
- No placeholders like [current offer]: every word is spoken aloud. End with a plain call to action with no price or
  offer (e.g. "Tap the link to try it tonight.")."""


def check(out: dict, x: dict, transcripts: list[dict] | None = None, presenter: bool = False) -> dict:
    evidence = " ".join(v["evidence"] for v in x["videos"])
    dropped: list[str] = []
    for line in out["lines"]:
        bad = _invented_figures(line["text"], evidence)
        if bad:
            dropped.append(f"figures not in the source videos ({', '.join(bad)}), replaced with [current offer]")
            line["text"] = _FIGURE.sub("[current offer]", line["text"])
    given = {f["id"]: f for tier in ("rule", "context", "test") for f in x[tier]}
    follows = []
    for f in out["follows"]:
        if f["id"] in given:
            follows.append({**f, "title": given[f["id"]]["title"]})
        # An avoid it applies is a blocker below; a non-id (the kind's own name) is not a finding.
        elif f["id"] not in {g["id"] for g in x["avoid"]} and re.fullmatch(r"[0-9a-f]{12}", f["id"]):
            dropped.append(f"said it follows an unknown finding ({f['id']})")
    never = {f["id"]: f for f in x["avoid"]}
    blocked_by_avoid = [f for f in out["follows"] if f["id"] in never]
    avoids = [{**a, "title": never[a["id"]]["title"]} for a in out.get("avoids", []) if a["id"] in never]
    unchecked = [{"id": f["id"], "title": f["title"]} for f in x["avoid"] if f["id"] not in {a["id"] for a in avoids}]
    leads = [f for f in follows if f["id"] in {g["id"] for g in x["test"]}]
    if len(leads) > 1:
        dropped.append("applied more than one unproven lead")
    spoken = " ".join(line["text"] for line in out["lines"])
    if len(re.findall(r"[A-Za-z0-9'-]+", spoken)) > MAX_WORDS:
        dropped.append(f"runs long: about {round(len(spoken.split()) / 2.2)} seconds read aloud; the limit is 30")
    near = closest(spoken, transcripts or [])
    # A presenter script is what will be said, so the video's rules apply to it here; a script
    # that breaks one cannot be made (the page says why and offers to write it again).
    blockers: list[str] = [f"applies something to avoid: {never[f['id']]['title']}" for f in blocked_by_avoid]
    for line in out["lines"]:
        for msg in compliance.problems(line["text"]):
            blockers.append(f"{msg}: “{line['text']}”")
    if presenter:
        n = len(re.findall(r"[A-Za-z0-9'-]+", spoken))
        limit = int((sd.MAX_S - sd.END_SILENCE_S) * 2)
        if n > limit:
            blockers.append(f"{n} words; a 30-second video holds at most {limit}")
        said = [t["transcript"] for t in transcripts or []]
        for line in out["lines"]:
            if "[" in line["text"] or "]" in line["text"]:
                blockers.append(f"has a placeholder that would be read out loud: “{line['text']}”")
            if sd.TESTIMONIAL.search(line["text"]):
                blockers.append(f"reads as a personal testimonial: “{line['text']}”")
            hit = sd.copied_line(line["text"], said)
            if hit:
                blockers.append(f"repeats a line from a video of this kind ({round(hit[0] * 100)}% alike): “{hit[1]}”")
    applied = {f["id"] for f in follows}
    missed = [{"id": f["id"], "title": f["title"]} for f in x["rule"] if f["id"] not in applied]
    return {
        "version": VERSION,
        "cluster": x["cluster"],
        "title": out["title"],
        "note": out["note"],
        "lines": out["lines"],
        "follows": follows,
        "missed": missed,
        "avoids": avoids,
        "unchecked_avoids": unchecked,
        "claims": out["claims"],
        "dropped": dropped,
        "from_videos": [
            {**{k: v[k] for k in ("footage_id", "name", "cpa")}, "watched": bool(v.get("watched"))} for v in x["videos"]
        ],
        "closest": near,
        "too_close": bool(near and near["ratio"] >= TOO_CLOSE),
        "presenter": presenter,
        "blockers": blockers,
        "compared_with": len(transcripts or []),
        "rules": len(x["rule"]),
    }


async def write(db: Database, client, settings, blobs, org_id: UUID, eid: UUID, cluster: str) -> dict:
    """Watch the best videos (cached per video), then one paid call for the script; stores and
    returns the checked script."""
    model = settings.brief_model
    presenter = sd.makeable(cluster)
    x = await inputs(db, org_id, eid, cluster, settings, blobs)
    if not x["videos"]:
        raise ValueError("this kind of video has no ranked best videos to learn its voice from")
    key = f"{eid}:{cluster}:{new_id()}"
    claim = await model_calls.claim(db, org_id, PURPOSE, key, model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=8000,
            messages=[{"role": "system", "content": SYSTEM + (PRESENTER if presenter else "")}, {"role": "user", "content": prompt(x)}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "creator_script", "schema": SCHEMA, "strict": True},
            },
        )
        out = json.loads(resp.choices[0].message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    u = resp.usage
    await model_calls.finish(db, claim.id, input_tokens=u.prompt_tokens, output_tokens=u.completion_tokens)
    payload = {**check(out, x, await cluster_transcripts(db, org_id, cluster), presenter), "model": model}
    await db.execute(
        "INSERT INTO experiment_result (experiment_id, key, payload) VALUES ($1,$2,$3)"
        " ON CONFLICT (experiment_id, key) DO UPDATE SET payload=$3, created_at=now()",
        eid,
        KEY + cluster,
        payload,
    )
    return payload
