"""Make a new video from a cluster's new-creator script, for the kinds a model can make without
changing what kind of video it is: voiceover over footage, text only, AI. A generated person
on camera would be an AI video, judged against the AI cluster, so talking-head kinds stay
scripts for a real creator.

Steps, each stored on the script_video row so a resumed job picks up where it stopped:
1. plan: one call splits the script into shots (1-3 lines each) with a visual prompt per shot,
   written from the watched shot lists of the kind's best videos: how they work, not what they
   show. No faces, no text in frame, never their setting or props.
2. voice: one generated voice reads each line (the length of each line sets its shot's length).
   Text-only kinds have no voice; each line gets reading time instead.
3. product: the product cropped out of a frame of a best video, where it is on screen (one
   small vision call picks the frame and the box). It is passed as a reference image, so the
   label comes out right while no frame, person or setting of an ad that ran is reused.
4. clips: one generated clip per shot through AI Gateway. The priciest step; each clip is
   ledgered and its operation id kept, so a resumed job asks for status and never re-sends.
5. assemble: clips cut or held to their lines, captions of each line, the voice under it (ffmpeg).
6. checks: a frame from each shot against frames of every video of the kind (difference hash);
   a shot that is too alike is flagged before the video can be used.

AI Gateway runs video only with at least $10 of credit; below that the row is 'blocked' with
the balance, and nothing was spent on clips.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from PIL import Image

from cs.briefs import render as render_mod
from cs.core import model_calls
from cs.core.db import Database
from cs.generate.opening import duration, run
from cs.jobs.registry import JobContext, handler

log = logging.getLogger(__name__)
JOB = "make.script_video"
VIDEO_MODEL = "alibaba/wan-v2.6-r2v-flash"
VIDEO_PRICE_PER_S = 0.05  # 720p, gateway /v1/models 2026-09-27
CLIP_SECONDS = (5, 10)
W, H = 720, 1280
TTS_MODEL = "google/gemini-3.8-flash-tts"
VOICES = ("Kore", "Puck", "Zephyr", "Charon", "Aoede", "Fenrir")
GAP_S = 0.15
READ_CPS = 14  # text-only: characters read per second
MIN_LINE_S = 1.6
TOO_ALIKE_BITS = 10  # of 64: difference-hash distance at or under this is "the same picture"
MAKEABLE = ("Voiceover over footage", "Text only", "AI")


def makeable(cluster: str) -> bool:
    return cluster.startswith(MAKEABLE)


def speaks(cluster: str) -> bool:
    return not cluster.startswith("Text only")


PRODUCT_FRAMES = 8
PREP_USD = 0.06  # plan, voice and product shot, measured 2026-09-27


def estimate(script: dict, cluster: str) -> dict:
    """Before anything runs: the spoken length (reading speed) and what the first step costs.
    The clip cost is only known once the shots are planned and the lines voiced; see clip_cost."""
    lines = script.get("lines") or []
    secs = sum(max(MIN_LINE_S, len(line["text"]) / READ_CPS) + GAP_S for line in lines)
    return {"seconds": round(secs, 1), "prep_usd": PREP_USD}


def clip_cost(p: dict, v: dict) -> dict:
    """The exact clip spend: each shot's lines set its length, rounded up to a clip length."""
    secs = [clip_seconds(sum(v["lines"][j] + GAP_S for j in s["lines"])) for s in p["shots"]]
    return {"shots": len(secs), "seconds": sum(secs), "usd": round(sum(secs) * VIDEO_PRICE_PER_S, 2),
            "video_seconds": round(sum(v["lines"]) + GAP_S * len(v["lines"]), 1)}


# ---- gateway helpers ---------------------------------------------------------------------


def _hdr(settings, model: str, kind: str) -> dict:
    return {
        "Authorization": f"Bearer {settings.ai_gateway_api_key}",
        "ai-gateway-protocol-version": "0.0.1",
        f"ai-{kind}-model-specification-version": "4",
        "ai-model-id": model,
        "Content-Type": "application/json",
    }


async def credit(settings) -> float | None:
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{settings.ai_gateway_base_url}/v1/credits",
                headers={"Authorization": f"Bearer {settings.ai_gateway_api_key}"},
            )
        return float(r.json()["balance"])
    except Exception:  # noqa: BLE001 - informational only
        return None


async def _chat_json(settings, model: str, messages: list, schema: dict, name: str) -> tuple[dict, dict]:
    async with httpx.AsyncClient(timeout=300) as c:
        r = await c.post(
            f"{settings.ai_gateway_base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.ai_gateway_api_key}"},
            json={
                "model": model,
                "messages": messages,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
                },
            },
        )
    r.raise_for_status()
    j = r.json()
    return json.loads(j["choices"][0]["message"]["content"] or "{}"), j.get("usage") or {}


async def _ledgered(db: Database, org_id: UUID, purpose: str, key: str, model: str, fn):
    """Run one paid call once. A second attempt at the same key raises instead of paying again."""
    if await model_calls.attempts_for(db, purpose, key):
        raise RuntimeError(f"{purpose} was already sent once for this video; paid calls are not re-sent")
    claim = await model_calls.claim(db, org_id, purpose, key, model)
    try:
        out, usage = await fn()
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc)[:1000])
        raise
    await model_calls.finish(
        db,
        claim.id,
        input_tokens=int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
    )
    return out


# ---- 1. plan ---------------------------------------------------------------------------------

PLAN_SYSTEM = """You turn a video ad script into a shot plan for a video model. The finished ad is the kind named:
it is carried by footage, not by a person talking to camera.
Split the script's lines, in order, into shots of 1 to 3 consecutive lines; every line in exactly one shot.
For each shot write `prompt`: what the camera sees, for a vertical 9:16 clip, in one or two concrete sentences
(subject, action, setting, light, camera move). Rules for every prompt:
- no faces: hands, objects, rooms and nature are fine; people only from behind or out of focus;
- no words, captions, logos or on-screen text in the frame (captions are added later);
- new settings and props: borrow how the best videos work (pace, what each moment does, when the product shows),
  never what is in them;
- `product` true when the product should be in the shot; describe it only as "the product" (a reference image of
  it is supplied).
Also pick `voice` (one of Kore, Puck, Zephyr, Charon, Aoede, Fenrir) and `delivery` (how it should be read, one line)."""

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "voice": {"type": "string"},
        "delivery": {"type": "string"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "lines": {"type": "array", "items": {"type": "integer"}},
                    "prompt": {"type": "string"},
                    "product": {"type": "boolean"},
                },
                "required": ["lines", "prompt", "product"],
            },
        },
    },
    "required": ["voice", "delivery", "shots"],
}


def check_plan(plan: dict, n_lines: int) -> dict:
    seen: list[int] = []
    shots = []
    for s in plan["shots"]:
        lines = [i for i in s["lines"] if 0 <= i < n_lines and i not in seen]
        if not lines or not s["prompt"].strip():
            continue
        seen += lines
        shots.append({**s, "lines": sorted(lines)})
    if sorted(seen) != list(range(n_lines)) or seen != sorted(seen):
        raise ValueError("the shot plan does not cover the script's lines once each, in order")
    voice = plan["voice"] if plan["voice"] in VOICES else VOICES[0]
    return {"voice": voice, "delivery": plan["delivery"], "shots": shots}


async def plan(db: Database, settings, row) -> dict:
    script = row["script"]
    lines = script["lines"]
    watched = []
    for v in script.get("from_videos") or []:
        t = await db.fetchval(
            "SELECT r.payload->>'text' FROM experiment_result r JOIN experiment e ON e.id = r.experiment_id"
            " WHERE e.org_id=$1 AND r.key LIKE $2 LIMIT 1",
            row["org_id"],
            f"watch:{v['footage_id']}:%",
        )
        if t:
            watched.append(t)
    text = "\n".join(
        [f"KIND: {row['cluster']}", "", "SCRIPT (numbered lines):"]
        + [f"[{i}] {line['t']} ({line['direction']}) {line['text']}" for i, line in enumerate(lines)]
        + [""]
        + [f"HOW BEST VIDEO {i} IS FILMED (watched):\n{w}\n" for i, w in enumerate(watched, 1)]
    )
    out = await _ledgered(
        db,
        row["org_id"],
        "make.script_video.plan",
        f"{row['id']}:plan",
        settings.brief_model,
        lambda: _chat_json(
            settings,
            settings.brief_model,
            [{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": text}],
            PLAN_SCHEMA,
            "shot_plan",
        ),
    )
    return check_plan(out, len(lines))


# ---- 2. voice --------------------------------------------------------------------------------


async def voice(db: Database, blobs, settings, row, p: dict, work: Path) -> dict:
    lines = row["script"]["lines"]
    if not speaks(row["cluster"]):
        return {"path": None, "lines": [max(MIN_LINE_S, len(x["text"]) / READ_CPS) for x in lines]}

    async def call():
        wavs = []
        async with httpx.AsyncClient(timeout=120) as c:
            for i, x in enumerate(lines):
                r = await c.post(
                    f"{settings.ai_gateway_base_url}/v4/ai/speech-model",
                    headers=_hdr(settings, TTS_MODEL, "speech"),
                    json={"text": x["text"], "voice": p["voice"], "instructions": p["delivery"], "outputFormat": "wav"},
                )
                r.raise_for_status()
                f = work / f"line{i}.wav"
                f.write_bytes(base64.b64decode(r.json()["audio"]))
                wavs.append(f)
        return wavs, {}

    wavs = await _ledgered(db, row["org_id"], "make.script_video.voice", f"{row['id']}:voice", TTS_MODEL, call)
    lengths = [round(duration(f), 3) for f in wavs]
    # One track: each line, then a short pause.
    gap = work / "gap.wav"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(GAP_S), str(gap)])
    parts = []
    for f in wavs:
        parts += [f, gap]
    listing = work / "voice.txt"
    listing.write_text("".join(f"file '{x}'\n" for x in parts))
    out = work / "voice.m4a"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-ac", "1", "-ar", "48000", "-c:a", "aac", str(out)])
    path = f"{row['org_id']}/script_video/{row['id']}/voice.m4a"
    await blobs.put(path, out.read_bytes(), "audio/mp4")
    return {"path": path, "lines": lengths, "voice": p["voice"]}


# ---- 3. product reference ----------------------------------------------------------------

PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "frame": {"type": "integer"},
        "box": {"type": "array", "items": {"type": "integer"}},
        "clear": {"type": "boolean"},
    },
    "required": ["frame", "box", "clear"],
}


async def product(db: Database, blobs, settings, row, work: Path) -> dict:
    """The product alone, cropped out of a frame where a best video shows it. Reused for the
    account once made."""
    prev = await db.fetchval(
        "SELECT product FROM script_video WHERE ad_account_id=$1 AND product IS NOT NULL ORDER BY created_at DESC LIMIT 1",
        row["ad_account_id"],
    )
    if prev:
        return prev
    frames: list[tuple[Path, str]] = []
    # The product is the account's, so any of its ads can show it: the kind's best videos first,
    # then the account's biggest spenders with product shots. The crop keeps the package only.
    pool = [UUID(v["footage_id"]) for v in row["script"].get("from_videos") or []]
    pool += [
        r["id"]
        for r in await db.fetch(
            """
            SELECT f.id FROM footage f
            JOIN footage_metric_window w ON w.footage_id = f.id AND w.window_kind = 'lifetime'
            WHERE f.ad_account_id = $1 AND f.format = 'video'
              AND EXISTS (SELECT 1 FROM scene s WHERE s.footage_id = f.id AND s.shows_product)
            ORDER BY w.spend DESC NULLS LAST LIMIT 6
            """,
            row["ad_account_id"],
        )
        if r["id"] not in pool
    ]
    for fid in pool:
        f = await db.fetchrow(
            "SELECT a.path FROM footage f JOIN creative c ON c.id=f.rep_creative_id JOIN asset a ON a.id=c.primary_asset_id WHERE f.id=$1",
            fid,
        )
        scenes = await db.fetch(
            "SELECT t_start_s, t_end_s FROM scene WHERE footage_id=$1 AND shows_product ORDER BY ordinal LIMIT 2",
            fid,
        )
        if not f or not scenes:
            continue
        src = work / f"src_{fid}.mp4"
        src.write_bytes(await blobs.get(f["path"]))
        for s in scenes:
            t = (s["t_start_s"] + (s["t_end_s"] or s["t_start_s"] + 1)) / 2
            out = work / f"cand{len(frames)}.jpg"
            run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1", "-vf", "scale=512:-2", str(out)])
            frames.append((out, str(fid)))
        if len(frames) >= PRODUCT_FRAMES:
            break
    if not frames:
        raise ValueError("no ad of this account has a product shot")
    content: list[dict] = [
        {
            "type": "text",
            "text": "Each image is numbered from 0. Pick the one where the product package is most clearly visible, "
            "upright and readable (not held at an angle, not mirrored), and give its box [x0, y0, x1, y1] on a 0-1000 "
            "scale around the package only, as tight as the package itself. Captions and people elsewhere in the frame "
            "are fine: keep them outside the box (if a caption touches the package, start the box below the caption). "
            "`clear` false only if no image shows the package clearly.",
        }
    ]
    for f, _ in frames:
        content.append(
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()}}
        )
    pick = await _ledgered(
        db,
        row["org_id"],
        "make.script_video.product",
        f"{row['id']}:product",
        settings.brief_model,
        lambda: _chat_json(settings, settings.brief_model, [{"role": "user", "content": content}], PRODUCT_SCHEMA, "product_box"),
    )
    if not pick["clear"] or not 0 <= pick["frame"] < len(frames) or len(pick["box"]) != 4:
        raise ValueError("no product shot in the account's ads shows the package clearly without text over it")
    img = Image.open(frames[pick["frame"]][0]).convert("RGB")
    x0, y0, x1, y1 = (max(0, min(1000, b)) for b in pick["box"])
    w, h = img.size
    pad = 0.015
    box = (
        int(max(0, x0 / 1000 - pad) * w),
        int(max(0, y0 / 1000 - pad) * h),
        int(min(1, x1 / 1000 + pad) * w),
        int(min(1, y1 / 1000 + pad) * h),
    )
    crop = img.crop(box)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    path = f"{row['org_id']}/script_video/product/{row['ad_account_id']}.png"
    await blobs.put(path, buf.getvalue(), "image/png")
    return {"path": path, "from_footage": frames[pick["frame"]][1], "size": list(crop.size)}


# ---- 4. clips --------------------------------------------------------------------------------


class Blocked(Exception):
    """AI Gateway will not run video with less than $10 of credit."""


def clip_seconds(need: float) -> int:
    return next((c for c in CLIP_SECONDS if c >= need), CLIP_SECONDS[-1])


async def clips(db: Database, blobs, settings, row, p: dict, v: dict, prod: dict) -> list[dict]:
    """One clip per shot. The start is ledgered and its operation id stored at once; a resumed
    job only polls. Returns every shot's clip with its blob path."""
    out: list[dict] = list(row["clips"] or [])
    by_shot = {c["shot"]: c for c in out}
    prod_url = await blobs.url_for(prod["path"], expires_seconds=7200)
    async with httpx.AsyncClient(timeout=120) as c:
        for i, s in enumerate(p["shots"]):
            need = sum(v["lines"][j] + GAP_S for j in s["lines"])
            cur = by_shot.get(i)
            if cur and cur.get("path"):
                continue
            if not cur:
                secs = clip_seconds(need)
                key = f"{row['id']}:shot{i}"
                if await model_calls.attempts_for(db, "make.script_video.clip", key):
                    raise RuntimeError(f"shot {i + 1} was already sent once with no operation kept; not re-sending")
                body = {
                    "prompt": s["prompt"] + " Vertical 9:16. No text, captions or logos in the frame. No faces.",
                    "n": 1,
                    "duration": secs,
                    "aspectRatio": "9:16",
                    "resolution": f"{W}x{H}",
                    "generateAudio": False,
                }
                if s["product"]:
                    body["inputReferences"] = [{"type": "url", "url": prod_url, "mediaType": "image/png"}]
                claim = await model_calls.claim(db, row["org_id"], "make.script_video.clip", key, row["video_model"])
                r = await c.post(
                    f"{settings.ai_gateway_base_url}/v4/ai/video-model/start",
                    headers=_hdr(settings, row["video_model"], "video"),
                    json=body,
                )
                if r.status_code == 402:
                    await model_calls.fail(db, claim.id, f"402: {r.text[:300]}")
                    raise Blocked(r.text[:300])
                if r.status_code >= 400:
                    await model_calls.fail(db, claim.id, f"{r.status_code}: {r.text[:500]}")
                    raise RuntimeError(f"shot {i + 1}: the video model refused it ({r.status_code}): {r.text[:300]}")
                cur = {"shot": i, "operation": r.json()["operation"], "seconds": secs, "need": round(need, 2), "claim": str(claim.id)}
                out.append(cur)
                by_shot[i] = cur
                await _save(db, row["id"], clips=out)
            # Poll (free) until this clip is ready.
            t0 = time.monotonic()
            while True:
                st = await c.post(
                    f"{settings.ai_gateway_base_url}/v4/ai/video-model/status",
                    headers=_hdr(settings, row["video_model"], "video"),
                    json={"operation": cur["operation"]},
                )
                j = st.json() if st.status_code == 200 else {"status": "pending"}
                if j["status"] == "completed":
                    got = j["videos"][0]
                    data = (await c.get(got["url"])).content if got["type"] == "url" else base64.b64decode(got["data"])
                    path = f"{row['org_id']}/script_video/{row['id']}/shot{i}.mp4"
                    await blobs.put(path, data, "video/mp4")
                    cur["path"] = path
                    await model_calls.finish(db, UUID(cur["claim"]))
                    await _save(db, row["id"], clips=out)
                    break
                if j["status"] == "error":
                    await model_calls.fail(db, UUID(cur["claim"]), json.dumps(j)[:1000])
                    raise RuntimeError(f"shot {i + 1}: the video model failed: {json.dumps(j)[:300]}")
                if time.monotonic() - t0 > 1200:
                    raise RuntimeError(f"shot {i + 1}: still generating after 20 minutes; run the job again to keep waiting")
                await asyncio.sleep(10)
    return out


# ---- 5. assemble -----------------------------------------------------------------------------


async def assemble(blobs, row, p: dict, v: dict, cl: list[dict], work: Path) -> tuple[bytes, list[float]]:
    lines = row["script"]["lines"]
    parts = []
    starts: list[float] = []  # each line's start in the finished video
    t = 0.0
    by_shot = {c["shot"]: c for c in cl}
    for i, s in enumerate(p["shots"]):
        need = 0.0
        for j in s["lines"]:
            starts.append(t + need)
            need += v["lines"][j] + GAP_S
        src = work / f"shot{i}.mp4"
        src.write_bytes(await blobs.get(by_shot[i]["path"]))
        out = work / f"fit{i}.mp4"
        hold = max(0.0, need - duration(src))
        run(
            [
                "ffmpeg", "-y", "-i", str(src), "-an",
                "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps=30,"
                f"tpad=stop_mode=clone:stop_duration={hold + 0.1:.2f}",
                "-t", f"{need:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(out),
            ]
        )
        parts.append(out)
        t += need
    listing = work / "shots.txt"
    listing.write_text("".join(f"file '{x}'\n" for x in parts))
    joined = work / "joined.mp4"
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(joined)])
    # Captions: each line on screen while it is read.
    inputs = ["-i", str(joined)]
    chain = "[0:v]"
    filters = []
    for j, line in enumerate(lines):
        png = work / f"cap{j}.png"
        render_mod.text_png(png, line["text"], W)
        inputs += ["-i", str(png)]
        end = starts[j] + v["lines"][j] + GAP_S
        nxt = f"[c{j}]"
        filters.append(
            f"{chain}[{j + 1}:v]overlay=x=(W-w)/2:y=H*0.70:enable='between(t,{starts[j]:.2f},{end:.2f})'{nxt}"
        )
        chain = nxt
    cmd = ["ffmpeg", "-y", *inputs]
    if v.get("path"):
        audio = work / "voice.m4a"
        audio.write_bytes(await blobs.get(v["path"]))
        cmd += ["-i", str(audio)]
    cmd += ["-filter_complex", ";".join(filters), "-map", chain]
    if v.get("path"):
        cmd += ["-map", f"{len(lines) + 1}:a", "-c:a", "aac", "-shortest"]
    final = work / "final.mp4"
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-movflags", "+faststart", str(final)]
    run(cmd)
    return final.read_bytes(), starts


# ---- 6. checks -------------------------------------------------------------------------------


def dhash(img: Image.Image) -> int:
    g = img.convert("L").resize((9, 8))
    px = list(g.getdata())
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def _frame(path: Path, t: float, out: Path) -> Image.Image | None:
    try:
        run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", str(out)])
        return Image.open(out)
    except Exception:  # noqa: BLE001 - a missing frame is skipped
        return None


async def checks(db: Database, blobs, row, final: Path, p: dict, starts: list[float], work: Path) -> dict:
    from cs.briefs.findings import cluster_members

    ids = [f for f, c in (await cluster_members(db, row["org_id"])).items() if c == row["cluster"]]
    theirs: list[tuple[int, str]] = []
    for r in await db.fetch(
        """
        SELECT f.id, (SELECT t.path FROM asset t WHERE t.creative_id = f.rep_creative_id AND t.kind = 'thumbnail'
                        ORDER BY t.seq LIMIT 1) AS poster
        FROM footage f WHERE f.id = ANY($1::uuid[])
        """,
        ids,
    ):
        if r["poster"]:
            try:
                theirs.append((dhash(Image.open(io.BytesIO(await blobs.get(r["poster"])))), str(r["id"])))
            except Exception:  # noqa: BLE001
                continue
    for v in row["script"].get("from_videos") or []:
        src = work / f"src_{v['footage_id']}.mp4"
        if not src.exists():
            f = await db.fetchval(
                "SELECT a.path FROM footage f JOIN creative c ON c.id=f.rep_creative_id JOIN asset a ON a.id=c.primary_asset_id WHERE f.id=$1",
                UUID(v["footage_id"]),
            )
            if f:
                src.write_bytes(await blobs.get(f))
        if src.exists():
            d = duration(src)
            for k in range(6):
                im = _frame(src, d * (k + 0.5) / 6, work / f"ref_{v['footage_id']}_{k}.jpg")
                if im is not None:
                    theirs.append((dhash(im), v["footage_id"]))
    shots = []
    for i, s in enumerate(p["shots"]):
        mid = starts[s["lines"][0]] + 0.8
        im = _frame(final, mid, work / f"mine{i}.jpg")
        if im is None or not theirs:
            continue
        h = dhash(im)
        dist, fid = min((bin(h ^ t).count("1"), f) for t, f in theirs)
        shots.append({"shot": i, "closest_bits": dist, "closest_footage": fid, "too_alike": dist <= TOO_ALIKE_BITS})
    return {"compared_with_frames": len(theirs), "videos": len(ids), "shots": shots, "too_alike": any(s["too_alike"] for s in shots)}


# ---- the job ---------------------------------------------------------------------------------


async def _save(db: Database, vid: UUID, **cols) -> None:
    sets = ", ".join(f"{k}=${i + 2}" for i, k in enumerate(cols))
    await db.execute(f"UPDATE script_video SET {sets}, updated_at=now() WHERE id=$1", vid, *cols.values())


@handler(JOB)
async def make_script_video(ctx: JobContext) -> None:
    vid = UUID(ctx.job.payload["script_video_id"])
    row = await ctx.db.fetchrow("SELECT * FROM script_video WHERE id=$1", vid)
    if row is None or row["status"] not in ("queued", "running", "blocked"):
        return
    db, blobs, settings = ctx.db, ctx.blobs, ctx.settings
    await _save(db, vid, status="running", error=None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            p = row["plan"]
            if p is None:
                await _save(db, vid, stage="Planning the shots")
                p = await plan(db, settings, row)
                await _save(db, vid, plan=p)
            v = row["voice"]
            if v is None:
                await _save(db, vid, stage="Recording the voice")
                v = await voice(db, blobs, settings, row, p, work)
                await _save(db, vid, voice=v)
            prod = row["product"]
            if prod is None:
                await _save(db, vid, stage="Finding the product")
                prod = await product(db, blobs, settings, row, work)
                await _save(db, vid, product=prod)
            cost = clip_cost(p, v)
            if row["approved_at"] is None:
                # Stop here with the exact clip cost; clips start only once approved.
                await _save(db, vid, status="confirm", stage=None, clip_cost_usd=cost["usd"])
                return
            bal = await credit(settings)
            if bal is not None and bal < 10:
                await _save(
                    db,
                    vid,
                    status="blocked",
                    stage=None,
                    error=f"AI Gateway runs video only with at least $10 of credit; it has ${bal:.2f}. "
                    "Top it up, then make it again. Nothing was spent on clips.",
                )
                return
            await _save(db, vid, stage="Generating the clips")
            row = await db.fetchrow("SELECT * FROM script_video WHERE id=$1", vid)
            try:
                cl = await clips(db, blobs, settings, row, p, v, prod)
            except Blocked as exc:
                await _save(db, vid, status="blocked", stage=None, error=f"AI Gateway refused the video call for credit: {exc}")
                return
            await _save(db, vid, stage="Putting it together")
            data, starts = await assemble(blobs, row, p, v, cl, work)
            path = f"{row['org_id']}/script_video/{vid}/final.mp4"
            await blobs.put(path, data, "video/mp4")
            final = work / "final.mp4"
            await _save(db, vid, stage="Checking it is new", path=path)
            chk = await checks(db, blobs, row, final, p, starts, work)
            await _save(db, vid, checks=chk, status="done", stage=None)
    except Exception as exc:  # noqa: BLE001 - shown on the page; paid steps are never re-sent
        log.exception("script video failed", extra={"script_video_id": str(vid)})
        await _save(db, vid, status="failed", stage=None, error=str(exc)[:1000])
