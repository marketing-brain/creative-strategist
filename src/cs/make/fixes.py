"""Fixes for an ad that runs: what What works says works for the ad's kind, placed where the ad
is weak. Most findings are about WORDS (the argument, the tone, a line, an offer card): those are
applied as an on-screen line over the stretch (free, rendered in code; most feeds play muted, so
text is how a running ad says something new). Findings about what is SEEN are applied as one
Seedance 2.5 shot laid over the stretch while the original sound plays on.

What works decides WHAT to change: every fix applies a finding of the ad's kind (a rule, a lead or
a shared trait, "do" direction), matched to the part of the video it is about by the playbook
choice's element (opening; story, argument, proof, tone and format → middle; offer and close →
close). The ad's own diagnosis decides WHERE: its opening, the stretch where viewers leave
fastest, or its close. A part with no finding gets at most one fix, marked as an untested idea and
listed apart, so an idea is never mistaken for evidence.

A fix only replaces the picture: the person's words and voice are untouched, so there is no lip
sync to break (ByteDance: editing what someone does or says is where edit models fail). Each fix is
planned first (one small call writes the shot; code writes the prompt and enforces the rules),
priced exactly, and generated only once a person approves it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from cs.briefs.findings import all_findings, cluster_of
from cs.core import model_calls
from cs.core.db import Database
from cs.core.ids import new_id
from cs.generate.opening import duration, run, size
from cs.jobs.registry import JobContext, handler
from cs.make import script_video as sv
from cs.make import seedance as sd

log = logging.getLogger(__name__)
JOB = "generate.fix"
PART_OF_ELEMENT = {
    "opening": "opening",
    "story": "middle",
    "argument": "middle",
    "proof": "middle",
    "tone": "middle",
    "format": "middle",
    "offer": "close",
    "close": "close",
}
KIND_OF_PART = {"opening": "hook", "middle": "cutaway", "close": "end"}
# A finding about what is said or written can only be applied with words; a shot cannot say it.
WORDS = re.compile(
    r"\b(script|says?|said|mention\w*|lines?|words?|argue\w*|argument|tone|voice|speaker|tells?|claims?|card|"
    r"discount|guarantee|hook|call(s|ing)? out|frames?|inform\w*|position\w*)\b",
    re.I,
)
LINE_WORDS = 9
READ_ON = {
    "opening": "Share still watching at 3s",
    "middle": "Click rate",
    "close": "Purchase rate",
}
TIER_RANK = {"rule": 0, "test": 1, "context": 2}
MIN_GEN_S = 4  # Seedance 2.5 generates 4-30 s; a shorter stretch uses the first seconds
MAX_STRETCH_S = 8
UNTESTED = {
    "opening": "The pouch is on screen in the first second, held in hand or on a surface, label towards the camera.",
    "middle": "A close-up that shows what the words describe at this moment, with the pouch in view.",
    "close": "The pouch in a real, tidy setting, label sharp and readable, as the ad ends.",
}


# ---- which fixes an ad gets -----------------------------------------------------------------


async def kind_findings(db: Database, org_id: UUID, cluster: str) -> list[dict]:
    """The kind's findings that say what to DO, each with the part of the video it is about."""
    eid = await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug='tag-clusters'", org_id)
    pb = await db.fetchval("SELECT payload FROM experiment_result WHERE experiment_id=$1 AND key='playbook'", eid) if eid else None
    if not pb:
        return []
    placed, choices = pb.get("findings", {}), pb.get("choices", {})
    out = []
    for fid, f in (await all_findings(db, org_id)).items():
        p = placed.get(fid)
        if f["cluster"] != cluster or not p or p["tier"] not in TIER_RANK or p["direction"] != "do":
            continue
        choice = choices.get(p["choice_key"]) or {}
        part = PART_OF_ELEMENT.get(choice.get("element", ""))
        if part:
            out.append(
                {
                    "finding_id": fid,
                    "title": f["title"],
                    "trait": p["trait"],
                    "tier": p["tier"],
                    "choice": choice.get("name"),
                    "part": part,
                    "visual": not WORDS.search(p["trait"]),
                }
            )
    return sorted(out, key=lambda f: TIER_RANK[f["tier"]])


def stretch(part: str, timeline: dict, drop: dict | None) -> tuple[float, float]:
    """The seconds a fix covers: the opening's first seconds, the fastest drop inside the middle
    (else its first line), or the seconds before the final end card."""
    d = float(timeline.get("duration") or 0)
    a, b = timeline["parts"][part]
    if part == "opening":
        return 0.0, round(min(max(b, 2.5), 3.5, d), 2)
    if part == "middle":
        if drop and a <= drop["start"] < b:
            s = drop["start"]
            e = min(max(drop["end"], s + 3), s + MAX_STRETCH_S, b)
            return round(s, 2), round(e, 2)
        first = next((x for x in timeline.get("sentences") or [] if x["start"] >= a), None)
        s = first["start"] if first else a
        return round(s, 2), round(min(s + 5, b), 2)
    e = max(a + 3, b - 3)  # leave the last seconds (usually the end card) untouched
    return round(max(a, e - 4), 2), round(e, 2)


async def candidates(db: Database, org_id: UUID, footage_id: UUID, verdict: dict, timeline: dict, drop: dict | None, focus_id: str | None) -> list[dict]:
    """One fix per part that a finding of the ad's kind speaks to, weak part first; an untested
    idea only for the weak part when no finding covers it."""
    cluster = await cluster_of(db, org_id, footage_id)
    if not cluster or not timeline.get("duration"):
        return []
    found = await kind_findings(db, org_id, cluster)
    weak = verdict.get("weak")
    out = []
    for part in ("opening", "middle", "close"):
        mine = [f for f in found if f["part"] == part]
        if focus_id:  # the finding someone came here with leads its part
            mine.sort(key=lambda f: f["finding_id"] != focus_id)
        f = mine[0] if mine else None
        if f is None and part != weak:
            continue
        s, e = stretch(part, timeline, drop)
        kind = "line" if f and not f["visual"] else KIND_OF_PART[part]
        out.append(
            {
                "key": f"{part}:{f['finding_id'] if f else 'idea'}",
                "part": part,
                "kind": kind,
                "start": s,
                "end": e,
                "finding_id": f["finding_id"] if f else None,
                "finding": f["title"] if f else None,
                "tier": f["tier"] if f else None,
                "trait": f["trait"] if f else UNTESTED[part],
                "untested": f is None,
                "read_on": READ_ON[part],
                "weak": part == weak,
                "cluster": cluster,
                "cost_usd": 0.0 if kind == "line" else round(max(MIN_GEN_S, math.ceil(e - s)) * sd.PRICE_PER_S, 2),
            }
        )
    out.sort(key=lambda x: (not x["weak"], x["untested"], ["opening", "middle", "close"].index(x["part"])))
    return out


# ---- planning -------------------------------------------------------------------------------

SYSTEM = """You write ONE shot for a video model. The shot is laid over a stretch of a vertical phone-filmed ad
while the ad's own voice keeps playing, so it must make sense under those words and apply the given finding.
Write:
- camera: shot type and at most ONE simple move, e.g. "close-up, slow push-in" or "medium shot, static".
- shot: one full sentence, the key action first: the subject, one action, the setting. The pouch appears as the
  product from the reference image. People appear only as hands or seen from behind, never a face (the ad's own
  presenter is not in this shot).
- light: one natural light source that fits a home phone video.
Match the look of the ad (setting, light, handheld phone feel) from its description. No words, captions or labels
other than the pouch's own label. Never use the words selfie, front camera or mirror.
Use only the facts in the evidence you are given; if something is not in it, leave it out."""

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "camera": {"type": "string", "description": "shot type and at most one simple move"},
        "shot": {"type": "string", "description": "one full sentence: subject, one action, setting"},
        "light": {"type": "string", "description": "one natural light source"},
    },
    "required": ["camera", "shot", "light"],
}


def fix_prompt(fix: dict, plan: dict, gen_s: int) -> str:
    """The Seedance prompt for one shot, in the official order. The ad's words are NOT quoted:
    Seedance turns quoted words into burned-in subtitles."""
    return "\n".join(
        [
            "Audio: none; this shot is silent and the ad's own sound plays over it.",
            sd.PRODUCT_LINE,
            f"Summary: one vertical 9:16 shot of {gen_s} seconds for a phone-filmed ad. It shows: {fix['trait']}",
            f"0-{gen_s} seconds: {sd._sentence(plan['camera'])} {sd._sentence(plan['shot'])}",
            f"Additional notes: {plan['light']}; natural handheld phone look; natural textures; people only as hands or "
            "seen from behind, with five fingers on each hand; the pouch keeps its label readable; plain, uncluttered "
            "lower third; no words anywhere except the pouch's own label; one continuous shot.",
        ]
    )


def check_plan(plan: dict) -> dict:
    if len(plan["shot"].split()) < 5:
        raise sd.PlanError(f"the shot is not a sentence of what happens: “{plan['shot']}”")
    for k in ("camera", "shot"):
        if sd.CAMERA_BANNED.search(plan[k]):
            plan[k] = sd.CAMERA_BANNED.sub("", plan[k]).strip() or "close-up, static"
    if re.search(r"\b(face|faces|smiles at|looks at the camera|talks to the camera)\b", plan["shot"], re.I):
        raise sd.PlanError("the shot shows a face; fixes show hands, objects and rooms only")
    return plan


async def plan_fix(db: Database, settings, org_id: UUID, footage_id: UUID, fix: dict, words: str, gid: UUID) -> dict:
    from cs.experiments.describe import video_evidence

    f = await db.fetchrow("SELECT id, duration_s, rep_creative_id FROM footage WHERE id=$1", footage_id)
    ev = (await video_evidence(db, f)).text[:5000] if f else ""
    text = "\n".join(
        [
            f"FINDING TO APPLY ({'an untested idea' if fix['untested'] else fix['tier']}): {fix.get('finding') or ''} — {fix['trait']}",
            f"THE STRETCH: {fix['start']:.1f}s to {fix['end']:.1f}s of the ad ({fix['part']}).",
            f"WORDS SAID DURING IT (for meaning only): {words or '(none)'}",
            "",
            "THE AD (for its look and setting):",
            ev,
        ]
    )
    out = await sv._ledgered(
        db, org_id, "make.fix.plan", f"{gid}:plan", settings.brief_model,
        lambda: sv._chat_json(
            settings, settings.brief_model,
            [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}],
            SCHEMA, "fix_shot",
        ),
    )
    plan = check_plan(out)
    gen_s = max(MIN_GEN_S, math.ceil(fix["end"] - fix["start"]))
    return {**plan, "gen_s": gen_s, "prompt": fix_prompt(fix, plan, gen_s), "cost_usd": round(gen_s * sd.PRICE_PER_S, 2)}


async def create_plan(db: Database, settings, org_id: UUID, ad_account_id: UUID, footage_id: UUID, fix: dict, words: str, user: str | None) -> UUID:
    """Plan one fix and store it waiting for approval ('confirm'), with its exact prompt and cost."""
    gid = new_id()
    plan = await plan_fix(db, settings, org_id, footage_id, fix, words, gid)
    await db.execute(
        "INSERT INTO generation (id, org_id, ad_account_id, footage_id, kind, prompt, model, resolution, window_s, status,"
        " stage, estimated_cost_usd, created_by, fix, plan) VALUES ($1,$2,$3,$4,'fix',$5,$6,'720p',$7,'confirm',NULL,$8,$9,$10,$11)",
        gid, org_id, ad_account_id, footage_id, plan["prompt"], sd.MODEL, fix["end"] - fix["start"], plan["cost_usd"], user, fix, plan,
    )
    return gid


# ---- on-screen lines (words findings) ------------------------------------------------------

LINE_SYSTEM = f"""You write ONE short on-screen line for a vertical video ad that is already running. It appears over
a stretch of the ad while the person keeps talking, to apply a finding about what works for this kind of ad. Most
viewers watch muted, so the line must say the point on its own.
- At most {LINE_WORDS} words, plain and punchy, in the ad's own voice; it must fit what is being said at that moment.
- Apply the finding given. Use only claims the ad already makes (its words and on-screen text); never a new health
  claim, number, price or offer. Never say the product cures, treats or prevents anything, never name a disease,
  never ask the viewer about a condition.
Use only the facts in the evidence you are given; if something is not in it, leave it out."""

LINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"text": {"type": "string", "description": f"the on-screen line, at most {LINE_WORDS} words"}},
    "required": ["text"],
}


async def write_line(db: Database, settings, org_id: UUID, footage_id: UUID, fix: dict, words: str, gid: UUID) -> dict:
    from cs.briefs.generate import _invented_figures
    from cs.experiments.describe import video_evidence
    from cs.make import compliance

    f = await db.fetchrow("SELECT id, duration_s, rep_creative_id FROM footage WHERE id=$1", footage_id)
    ev = (await video_evidence(db, f)).text[:6000] if f else ""
    text = "\n".join(
        [
            f"FINDING TO APPLY ({fix['tier']}): {fix.get('finding') or ''} — {fix['trait']}",
            f"THE STRETCH: {fix['start']:.1f}s to {fix['end']:.1f}s ({fix['part']}).",
            f"WORDS SAID DURING IT: {words or '(none)'}",
            "",
            "THE AD (its words, on-screen text and claims):",
            ev,
        ]
    )
    out = await sv._ledgered(
        db, org_id, "make.fix.line", f"{gid}:line", settings.brief_model,
        lambda: sv._chat_json(
            settings, settings.brief_model,
            [{"role": "system", "content": LINE_SYSTEM}, {"role": "user", "content": text}],
            LINE_SCHEMA, "fix_line",
        ),
    )
    line = out["text"].strip().strip('"“”')
    if not line or len(line.split()) > LINE_WORDS:
        raise sd.PlanError(f"the line is {len(line.split())} words; at most {LINE_WORDS} fit on screen")
    bad = _invented_figures(line, ev)
    if bad:
        raise sd.PlanError(f"the line uses figures the ad does not: {', '.join(bad)}")
    problems = compliance.problems(line)
    if problems:
        raise sd.PlanError(problems[0])
    return {"text": line}


async def create_line(db: Database, settings, org_id: UUID, ad_account_id: UUID, footage_id: UUID, fix: dict, words: str, user: str | None) -> UUID:
    """Write the line and render it straight away: rendering is free, so there is no approval step."""
    gid = new_id()
    plan = await write_line(db, settings, org_id, footage_id, fix, words, gid)
    await db.execute(
        "INSERT INTO generation (id, org_id, ad_account_id, footage_id, kind, prompt, model, resolution, window_s, status,"
        " stage, estimated_cost_usd, created_by, fix, plan, approved_at) VALUES ($1,$2,$3,$4,'fix',$5,'ffmpeg','720p',$6,"
        "'queued','Rendering the line',0,$7,$8,$9,now())",
        gid, org_id, ad_account_id, footage_id, plan["text"], fix["end"] - fix["start"], user, fix, plan,
    )
    return gid


# ---- generating and fitting -----------------------------------------------------------------


def overlay(original: Path, shot: Path, start: float, end: float, out: Path) -> None:
    """The shot over [start, end] of the original picture, scaled to the original's frame; the
    original sound plays throughout."""
    w, h = size(original)
    span = end - start
    run([
        "ffmpeg", "-y", "-i", str(original), "-i", str(shot), "-filter_complex",
        f"[1:v]trim=0:{span:.3f},setpts=PTS-STARTPTS+{start:.3f}/TB,scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},fps=30[s];[0:v][s]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})':eof_action=pass[v]",
        "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(out),
    ])


def compare(original: Path, result: Path, start: float, end: float, out: Path) -> None:
    """Before and after, side by side, from a second before the fix to a second after it."""
    a, b = max(0.0, start - 1), end + 1
    w, h = size(original)
    half_w = 360
    half_h = int(round(half_w * h / w / 2) * 2)
    run([
        "ffmpeg", "-y", "-i", str(original), "-i", str(result), "-filter_complex",
        f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS,scale={half_w}:{half_h},fps=30[l];"
        f"[1:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS,scale={half_w}:{half_h},fps=30[r];[l][r]hstack[v];"
        f"[1:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS[aud]",
        "-map", "[v]", "-map", "[aud]", "-c:v", "libx264", "-crf", "20", "-c:a", "aac", "-movflags", "+faststart", str(out),
    ])


async def _generate(db: Database, blobs, settings, g, prod: dict) -> bytes:
    plan = g["plan"]
    key = f"{g['id']}:{sd.MODEL}"
    if await model_calls.attempts_for(db, "make.fix.generate", key):
        raise RuntimeError("this fix was already sent once; paid calls are not re-sent")
    claim = await model_calls.claim(db, g["org_id"], "make.fix.generate", key, sd.MODEL)
    headers = sv._hdr(settings, sd.MODEL, "video")
    ref = await sv._ref_url(blobs, {"org_id": g["org_id"], "id": g["id"]}, prod)
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            f"{settings.ai_gateway_base_url}/v4/ai/video-model/start",
            headers={**headers, "idempotency-key": key},
            json={
                "prompt": plan["prompt"], "n": 1, "duration": plan["gen_s"], "aspectRatio": "9:16",
                "resolution": sd.RESOLUTION, "generateAudio": False,
                "inputReferences": [{"type": "url", "url": ref, "mediaType": "image/png"}],
            },
        )
        if r.status_code >= 400:
            await model_calls.fail(db, claim.id, f"{r.status_code}: {r.text[:500]}")
            raise RuntimeError(f"the video model refused it ({r.status_code}): {r.text[:300]}")
        op = r.json()["operation"]
        await db.execute("UPDATE generation SET prediction_id=$2 WHERE id=$1", g["id"], json.dumps(op))
        t0 = time.monotonic()
        while time.monotonic() - t0 < 1500:
            await asyncio.sleep(10)
            st = await c.post(f"{settings.ai_gateway_base_url}/v4/ai/video-model/status", headers=headers, json={"operation": op})
            j = st.json() if st.status_code == 200 else {"status": "pending"}
            if j["status"] == "completed":
                v = j["videos"][0]
                data = (await c.get(v["url"])).content if v["type"] == "url" else base64.b64decode(v["data"])
                await model_calls.finish(db, claim.id)
                return data
            if j["status"] == "error":
                await model_calls.fail(db, claim.id, json.dumps(j)[:1000])
                raise RuntimeError(f"the video model failed: {str(j.get('error'))[:300]}")
    await model_calls.fail(db, claim.id, "still generating after 25 minutes")
    raise RuntimeError("still generating after 25 minutes")


async def _product(db: Database, blobs, settings, g) -> dict:
    """The account's product reference (the cleaned pouch once one exists)."""
    prev = await db.fetchval(
        "SELECT product FROM script_video WHERE ad_account_id=$1 AND product IS NOT NULL ORDER BY created_at DESC LIMIT 1",
        g["ad_account_id"],
    )
    if prev:
        return prev
    with tempfile.TemporaryDirectory() as tmp:
        row = {"id": g["id"], "org_id": g["org_id"], "ad_account_id": g["ad_account_id"], "script": {"from_videos": []}}
        return await sv.product(db, blobs, settings, row, Path(tmp))


async def _source(db: Database, blobs, footage_id: UUID) -> bytes:
    """The file that ran if it was uploaded, else the stored copy."""
    original_path = await db.fetchval(
        "SELECT original_path FROM creative_brief WHERE footage_id=$1 AND original_path IS NOT NULL ORDER BY created_at DESC LIMIT 1",
        footage_id,
    )
    path = original_path or await db.fetchval(
        "SELECT a.path FROM footage f JOIN creative c ON c.id = f.rep_creative_id JOIN asset a ON a.id = c.primary_asset_id WHERE f.id=$1",
        footage_id,
    )
    return await blobs.get(path)


async def _render_line(db: Database, blobs, g) -> None:
    """The on-screen line over its stretch, rendered with the brief renderer (free)."""
    from cs.briefs import render as render_mod

    gid, fix = g["id"], g["fix"]
    await db.execute("UPDATE generation SET status='running', stage='Rendering the line' WHERE id=$1", gid)
    try:
        src = await _source(db, blobs, g["footage_id"])
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original = work / "original.mp4"
            original.write_bytes(src)
            d = duration(original)
            edit = {"segments": [[0, d]], "overlays": [{"text": g["plan"]["text"], "start": fix["start"], "end": fix["end"]}], "hold_end": 0}
            data, _meta = await asyncio.to_thread(render_mod.render, src, edit, "")
            result = work / "result.mp4"
            result.write_bytes(data)
            cmp_ = work / "compare.mp4"
            compare(original, result, fix["start"], fix["end"], cmp_)
            base = f"{g['org_id']}/generate/{gid}"
            await blobs.put(f"{base}/result.mp4", result.read_bytes(), "video/mp4")
            await blobs.put(f"{base}/compare.mp4", cmp_.read_bytes(), "video/mp4")
        await db.execute(
            "UPDATE generation SET status='done', stage=NULL, result_path=$2, compare_path=$3, finished_at=now() WHERE id=$1",
            gid, f"{base}/result.mp4", f"{base}/compare.mp4",
        )
    except Exception as exc:  # noqa: BLE001 - shown on the page; rendering again is free
        log.exception("line fix failed", extra={"generation_id": str(gid)})
        await db.execute("UPDATE generation SET status='failed', stage=NULL, error=$2, finished_at=now() WHERE id=$1", gid, str(exc)[:1000])


@handler(JOB)
async def make_fix(ctx: JobContext) -> None:
    gid = UUID(ctx.job.payload["generation_id"])
    db, blobs, settings = ctx.db, ctx.blobs, ctx.settings
    g = await db.fetchrow("SELECT * FROM generation WHERE id=$1", gid)
    if g is None or g["status"] != "queued":
        return
    if (g["fix"] or {}).get("kind") == "line":
        await _render_line(db, blobs, g)
        return
    await db.execute("UPDATE generation SET status='running', stage='Generating the shot with Seedance 2.5' WHERE id=$1", gid)
    try:
        bal = await sv.credit(settings)
        if bal is not None and bal < 10:
            raise RuntimeError(f"AI Gateway runs video only with at least $10 of credit; it has ${bal:.2f}. Nothing was spent.")
        fix = g["fix"]
        prod = await _product(db, blobs, settings, g)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original = work / "original.mp4"
            original.write_bytes(await _source(db, blobs, g["footage_id"]))
            shot = work / "shot.mp4"
            shot.write_bytes(await _generate(db, blobs, settings, g, prod))
            await db.execute("UPDATE generation SET stage='Checking the label and fitting it in' WHERE id=$1", gid)
            frame = work / "label.jpg"
            run(["ffmpeg", "-y", "-ss", f"{min(duration(shot) / 2, 2):.2f}", "-i", str(shot), "-frames:v", "1", "-vf", "scale=720:-2", str(frame)])
            look = await sv.mirror_check(db, settings, g["org_id"], f"{gid}:mirror", frame, work)
            if look["mirrored"]:
                flipped = work / "shot_flipped.mp4"
                run(["ffmpeg", "-y", "-i", str(shot), "-vf", "hflip", "-c:v", "libx264", "-crf", "17", "-an", str(flipped)])
                shot = flipped
            result = work / "result.mp4"
            overlay(original, shot, fix["start"], fix["end"], result)
            cmp_ = work / "compare.mp4"
            compare(original, result, fix["start"], fix["end"], cmp_)
            pseudo = {"org_id": g["org_id"], "cluster": fix["cluster"], "script": {"from_videos": []}}
            chk = await sv.checks(db, blobs, pseudo, shot, {"shots": [{"lines": [0]}]}, [0.0], work)
            base = f"{g['org_id']}/generate/{gid}"
            await blobs.put(f"{base}/shot.mp4", shot.read_bytes(), "video/mp4")
            await blobs.put(f"{base}/result.mp4", result.read_bytes(), "video/mp4")
            await blobs.put(f"{base}/compare.mp4", cmp_.read_bytes(), "video/mp4")
        notes = ["Label came out mirrored; flipped."] if look["mirrored"] else []
        if chk.get("too_alike"):
            notes.append("The shot looks like a video of this kind that already ran.")
        await db.execute(
            "UPDATE generation SET status='done', stage=NULL, raw_path=$2, result_path=$3, compare_path=$4, notes=$5,"
            " finished_at=now() WHERE id=$1",
            gid, f"{base}/shot.mp4", f"{base}/result.mp4", f"{base}/compare.mp4", notes,
        )
    except Exception as exc:  # noqa: BLE001 - shown on the page; the paid call is not re-sent
        log.exception("fix failed", extra={"generation_id": str(gid)})
        await db.execute(
            "UPDATE generation SET status='failed', stage=NULL, error=$2, finished_at=now() WHERE id=$1", gid, str(exc)[:1000]
        )
