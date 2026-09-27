"""Make a new video from a kind's script: one Seedance 2.5 generation (cs.make.seedance), in the
form the kind needs (a presenter on camera; shots with an off-screen narrator; shots with the words
as captions). Expert, interview and chat kinds are never made.

Steps, each stored on the script_video row so a resumed job picks up where it stopped:
1. plan: the model directs one beat per script line (camera, what happens, emotion); code puts in
   the exact words, times them and writes the prompt, enforcing every rule before any spend.
2. product: the pouch cut out of one of the account's ads, used as the reference image.
3. confirm: stop with the exact cost; nothing is generated until a person approves it.
4. generate: one ledgered call; its operation id is kept so a resumed job only asks for status.
5. finish: unmirror the label if needed (a comparison with the flipped frame), trim the end, burn
   in captions of the script, hear what was said.
6. checks: frames of the video against frames of every video of the kind (difference hash).

AI Gateway runs video only with at least $10 of credit; below that the row is 'blocked' with the
balance, and nothing is spent.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import random
import re
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
from cs.make import seedance as sd

log = logging.getLogger(__name__)
JOB = "make.script_video"
CAPTION_W = 0.78  # caption box width as a share of the frame; smaller text, less of the picture covered
W, H = 720, 1280
TOO_ALIKE_BITS = 10  # of 64: difference-hash distance at or under this is "the same picture"
PRODUCT_FRAMES = 8
MIN_REF_PIXELS = 250_000
PREP_USD = 0.03  # planning, the product shot, the label check and hearing it back


def makeable(cluster: str) -> bool:
    return sd.makeable(cluster)


def mode(cluster: str) -> str | None:
    return sd.mode(cluster)


def estimate(script: dict, cluster: str) -> dict:
    """Before anything runs: roughly how long the script runs and what the first step costs. The
    video is priced once planned (about $0.23 a second at 720p, 30 s at most)."""
    m = sd.mode(cluster)
    n = sum(sd.words(line["text"]) for line in script.get("lines") or [])
    per = sd.WORDS_PER_S if m and sd.speaks(m) else sd.READ_WORDS_PER_S
    return {"seconds": round(min(n / per + sd.END_SILENCE_S, sd.MAX_S), 1), "prep_usd": PREP_USD, "mode": m}


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


# ---- 3. product reference ----------------------------------------------------------------

PRODUCT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "frame": {"type": "integer", "description": "the image's number, from 0"},
        "box_2d": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "[ymin, xmin, ymax, xmax] of the package, normalized to 0-1000",
        },
        "clear": {"type": "boolean"},
    },
    "required": ["frame", "box_2d", "clear"],
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
            "upright and readable (not held at an angle, not mirrored), and give its box_2d as [ymin, xmin, ymax, xmax] "
            "normalized to 0-1000, around the package only, as tight as the package itself. Captions and people elsewhere in the frame "
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
    if not pick["clear"] or not 0 <= pick["frame"] < len(frames) or len(pick["box_2d"]) != 4:
        raise ValueError("no product shot in the account's ads shows the package clearly without text over it")
    img = Image.open(frames[pick["frame"]][0]).convert("RGB")
    # Gemini's native box order is [ymin, xmin, ymax, xmax] on 0-1000 (ai.google.dev image understanding).
    y0, x0, y1, x1 = (max(0, min(1000, b)) for b in pick["box_2d"])
    w, h = img.size
    pad = 0.015
    box = (
        int(max(0, x0 / 1000 - pad) * w),
        int(max(0, y0 / 1000 - pad) * h),
        int(min(1, x1 / 1000 + pad) * w),
        int(min(1, y1 / 1000 + pad) * h),
    )
    crop = img.crop(box)
    # Reference rules (Seedance 2.5 tutorial): each side 300-6000 px, at least 90,000 px, and no
    # larger than the output (bigger references leave texture artifacts).
    f = max(1.0, math.sqrt(MIN_REF_PIXELS / (crop.width * crop.height)), 320 / min(crop.size))
    f = min(f, 720 / max(crop.size) if max(crop.size) * f > 720 else f)
    if f != 1.0:
        crop = crop.resize((math.ceil(crop.width * f), math.ceil(crop.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG")
    path = f"{row['org_id']}/script_video/product/{row['ad_account_id']}.png"
    await blobs.put(path, buf.getvalue(), "image/png")
    return {"path": path, "from_footage": frames[pick["frame"]][1], "size": list(crop.size)}


# ---- generation --------------------------------------------------------------------------------


class Blocked(Exception):
    """AI Gateway will not run video with less than $10 of credit."""


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


# ---- Seedance: plan, generate, finish ----------------------------------------------


async def _evidence(db: Database, script: dict) -> tuple[str, list[str]]:
    """The best videos' evidence (for claims and figures) and their watched shot lists."""
    from cs.experiments.describe import video_evidence

    ev, watched = [], []
    for v in script.get("from_videos") or []:
        f = await db.fetchrow("SELECT id, duration_s, rep_creative_id FROM footage WHERE id=$1", UUID(v["footage_id"]))
        if f:
            ev.append((await video_evidence(db, f)).text)
        t = await db.fetchval(
            "SELECT payload->>'text' FROM experiment_result WHERE key LIKE $1 LIMIT 1", f"watch:{v['footage_id']}:%"
        )
        if t:
            watched.append(t)
    return "\n\n".join(ev), watched


async def sd_plan(db: Database, settings, row) -> dict:
    script = row["script"]
    evidence, watched = await _evidence(db, script)
    text = "\n".join(
        [f"KIND OF VIDEO: {row['cluster']}", "", f"SCRIPT: {script.get('title')}. {script.get('note')}"]
        + ["SCRIPT LINES, numbered (code puts these exact words in; you direct each one):"]
        + [f"[{i}] ({line['direction']}) {line['text']}" for i, line in enumerate(script["lines"])]
        + ["", "SOURCE VIDEOS (for how they are filmed; the words come only from the script):", evidence[:9000]]
        + [f"\nHOW BEST VIDEO {i} IS FILMED (watched):\n{w}" for i, w in enumerate(watched, 1)]
    )
    m = sd.mode(row["cluster"])
    out = await _ledgered(
        db,
        row["org_id"],
        "make.script_video.plan",
        f"{row['id']}:{m}_plan",
        settings.brief_model,
        lambda: _chat_json(
            settings,
            settings.brief_model,
            [{"role": "system", "content": sd.system(m)}, {"role": "user", "content": text}],
            sd.schema(m),
            f"{m}_plan",
        ),
    )
    from cs.experiments.creator_script import cluster_transcripts

    said = [t["transcript"] for t in await cluster_transcripts(db, row["org_id"], row["cluster"])]
    checked = sd.check(out, evidence, said, [line["text"] for line in script["lines"]], m)
    return {**checked, "prompt": sd.prompt(checked), "compared_with": len(said)}


def fit_reference(data: bytes) -> bytes:
    """The product image within Seedance 2.5's reference rules: each side 300-6000 px, at least
    90,000 px, and no larger than the 720x1280 output (bigger leaves texture artifacts)."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    f = max(1.0, math.sqrt(MIN_REF_PIXELS / (im.width * im.height)), 320 / min(im.size))
    f = min(f, 720 / max(im.size)) if max(im.size) * f > 720 else f
    if f != 1.0:
        im = im.resize((round(im.width * f), round(im.height * f)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


async def _ref_url(blobs, row, prod: dict) -> str:
    key = f"{row['org_id']}/script_video/{row['id']}/product_ref.png"
    await blobs.put(key, fit_reference(await blobs.get(prod["path"])), "image/png")
    return await blobs.url_for(key, expires_seconds=7200)


async def sd_generate(db: Database, blobs, settings, row, p: dict, prod: dict, work: Path) -> Path:
    """The one Seedance generation. Its start is ledgered and its operation kept, so a resumed
    job only polls; the idempotency key stops the gateway billing a repeat start."""
    out: list[dict] = list(row["clips"] or [])
    cur = next((c for c in out if c.get("shot") == 0 and not c.get("error")), None)
    async with httpx.AsyncClient(timeout=120) as c:
        if cur is None:
            key = f"{row['id']}:{p['mode']}:{sd.MODEL}"
            if await model_calls.attempts_for(db, "make.script_video.clip", key):
                raise RuntimeError("this video was already sent once with no operation kept; not re-sending")
            body = {
                "prompt": p["prompt"],
                "n": 1,
                "duration": p["duration"],
                "aspectRatio": "9:16",
                "resolution": sd.RESOLUTION,
                "generateAudio": True,
                "inputReferences": [
                    {"type": "url", "url": await _ref_url(blobs, row, prod), "mediaType": "image/png"}
                ],
            }
            claim = await model_calls.claim(db, row["org_id"], "make.script_video.clip", key, sd.MODEL)
            r = await c.post(
                f"{settings.ai_gateway_base_url}/v4/ai/video-model/start",
                headers={**_hdr(settings, sd.MODEL, "video"), "idempotency-key": key},
                json=body,
            )
            if r.status_code == 402:
                await model_calls.fail(db, claim.id, f"402: {r.text[:300]}")
                raise Blocked(r.text[:300])
            if r.status_code >= 400:
                await model_calls.fail(db, claim.id, f"{r.status_code}: {r.text[:500]}")
                raise RuntimeError(f"the video model refused it ({r.status_code}): {r.text[:300]}")
            cur = {"shot": 0, "model": sd.MODEL, "operation": r.json()["operation"], "seconds": p["duration"], "claim": str(claim.id)}
            out.append(cur)
            await _save(db, row["id"], clips=out)
        if not cur.get("path"):
            t0 = time.monotonic()
            while True:
                st = await c.post(
                    f"{settings.ai_gateway_base_url}/v4/ai/video-model/status",
                    headers=_hdr(settings, sd.MODEL, "video"),
                    json={"operation": cur["operation"]},
                )
                j = st.json() if st.status_code == 200 else {"status": "pending"}
                if j["status"] == "completed":
                    got = j["videos"][0]
                    data = (await c.get(got["url"])).content if got["type"] == "url" else base64.b64decode(got["data"])
                    cur["path"] = f"{row['org_id']}/script_video/{row['id']}/raw.mp4"
                    await blobs.put(cur["path"], data, "video/mp4")
                    cur["warnings"] = j.get("warnings") or []
                    await model_calls.finish(db, UUID(cur["claim"]))
                    await _save(db, row["id"], clips=out)
                    break
                if j["status"] == "error":
                    await model_calls.fail(db, UUID(cur["claim"]), json.dumps(j)[:1000])
                    cur["error"] = str(j.get("error"))[:300]
                    await _save(db, row["id"], clips=out)
                    raise RuntimeError(f"the video model failed: {str(j.get('error'))[:300]}")
                if time.monotonic() - t0 > 1500:
                    raise RuntimeError("still generating after 25 minutes; run the job again to keep waiting")
                await asyncio.sleep(10)
    raw = work / "raw.mp4"
    raw.write_bytes(await blobs.get(cur["path"]))
    return raw


def burn_captions(src: Path, out: Path, items: list[tuple[str, float, float]], work: Path) -> None:
    """Captions over a finished video, each line on screen while it is said. Added after
    generation: Seedance renders subtitles unreliably, and most feeds play muted."""
    inputs, filters, chain = ["-i", str(src)], [], "[0:v]"
    for j, (text, a, b) in enumerate(items):
        png = work / f"th_cap{j}.png"
        render_mod.text_png(png, text, int(W * CAPTION_W))
        inputs += ["-i", str(png)]
        filters.append(f"{chain}[{j + 1}:v]overlay=x=(W-w)/2:y=H*0.78:enable='between(t,{a:.2f},{b:.2f})'[c{j}]")
        chain = f"[c{j}]"
    if not filters:
        out.write_bytes(src.read_bytes())
        return
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters), "-map", chain, "-map", "0:a?", "-c:v", "libx264",
         "-crf", "17", "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)])


async def mirror_check(db: Database, settings, org_id: UUID, key: str, frame: Path, work: Path) -> dict:
    """Is the product's label mirror-reversed in this frame? Vision models judge orientation poorly
    from one image (RotBench, 2025), so this is a comparison: the frame and its mirror image, in
    random order; which one reads normally? One small ledgered call."""
    flipped = work / f"{frame.stem}_flipped.jpg"
    Image.open(frame).transpose(Image.FLIP_LEFT_RIGHT).save(flipped)
    first_is_original = random.random() < 0.5
    pair = [frame, flipped] if first_is_original else [flipped, frame]
    b64 = lambda f: "data:image/jpeg;base64," + base64.b64encode(f.read_bytes()).decode()  # noqa: E731
    pick = await _ledgered(
        db, org_id, "make.script_video.mirror", key, settings.brief_model,
        lambda: _chat_json(
            settings, settings.brief_model,
            [{"role": "user", "content": [
                {"type": "text", "text": "Image A:"},
                {"type": "image_url", "image_url": {"url": b64(pair[0])}},
                {"type": "text", "text": "Image B:"},
                {"type": "image_url", "image_url": {"url": b64(pair[1])}},
                {"type": "text", "text": "One of these two images is the mirror image of the other. Transcribe the largest "
                 "words printed on the product package in each, exactly as they appear, then say which image shows that "
                 "text reading normally, left to right."},
            ]}],
            sd.MIRROR_SCHEMA, "mirror_check",
        ),
    )
    normal_is_a = pick["reads_normally"] == "A"
    return {"mirrored": normal_is_a != first_is_original, "label_readable": bool(pick["text_a"].strip() or pick["text_b"].strip())}


async def sd_finish(db: Database, settings, row, p: dict, raw: Path, work: Path) -> tuple[Path, dict]:
    """After generation: unmirror the label if needed, trim the end glitch, and hear what was said."""
    from cs.briefs.words import MODEL as WHISPER
    from cs.briefs.words import audio_mp3

    notes: dict[str, Any] = {}
    # 1. Is the label mirror-reversed? One small vision call on a frame that shows the product.
    at = next((b["start"] + (b["end"] - b["start"]) / 2 for b in p["beats"] if "label" in b["action"].lower()), p["duration"] * 0.4)
    frame = work / "label.jpg"
    run(["ffmpeg", "-y", "-ss", f"{at:.2f}", "-i", str(raw), "-frames:v", "1", "-vf", "scale=720:-2", str(frame)])
    look = await mirror_check(db, settings, row["org_id"], f"{row['id']}:mirror", frame, work)
    notes["mirrored"] = look["mirrored"]
    notes["label_readable"] = look["label_readable"]
    # 2. Flip if mirrored; trim the last 0.1 s (a short glitch is common at the very end).
    d = duration(raw)
    final = work / "final.mp4"
    vf = ["-vf", "hflip"] if look["mirrored"] else []
    run(["ffmpeg", "-y", "-i", str(raw), *vf, "-t", f"{max(1.0, d - 0.1):.3f}", "-c:v", "libx264", "-crf", "17",
         "-preset", "slow", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", str(final)])
    # 3. Captions of the script, timed to its beats, for the muted feed.
    captioned = work / "captioned.mp4"
    burn_captions(final, captioned, [(b["line"], b["start"], b["end"]) for b in p["beats"] if b["line"].strip()], work)
    final = captioned
    # 4. What was actually said, against the lines.
    async def hear():
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                f"{settings.ai_gateway_base_url}/v4/ai/transcription-model",
                headers={**_hdr(settings, WHISPER, "transcription")},
                json={"audio": base64.b64encode(audio_mp3(final.read_bytes())).decode(), "mediaType": "audio/mpeg"},
            )
        r.raise_for_status()
        return r.json(), {}
    if not sd.speaks(p.get("mode", "talking_head")):
        return final, notes
    try:
        heard = (await _ledgered(db, row["org_id"], "make.script_video.hear", f"{row['id']}:hear", WHISPER, hear)).get("text", "")
    except Exception as exc:  # noqa: BLE001 - the check is informative; the video stands
        heard = ""
        notes["hear_error"] = str(exc)[:200]
    meant = " ".join(b["line"] for b in p["beats"] if b["line"].strip())
    from difflib import SequenceMatcher

    norm = lambda t: re.sub(r"[^a-z0-9 ]+", " ", t.lower()).split()  # noqa: E731
    notes["heard"] = heard
    notes["meant"] = meant
    notes["said_right"] = round(SequenceMatcher(None, norm(meant), norm(heard)).ratio(), 3) if heard else None
    return final, notes


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
            if sd.mode(row["cluster"]) is None:
                await _save(db, vid, status="failed", stage=None, error="This kind of video is not made by a model.")
                return
            p = row["plan"]
            if p is None or "beats" not in p:  # plans from the earlier clip pipeline are planned again
                await _save(db, vid, stage="Planning the video")
                try:
                    p = await sd_plan(db, settings, row)
                except sd.PlanError as exc:
                    await _save(db, vid, status="failed", stage=None, error=f"The plan broke a rule, so nothing was generated: {exc}")
                    return
                # A new plan is a new price: it always stops for approval again.
                await _save(db, vid, plan=p, voice={"native": sd.speaks(p["mode"])}, clips=[], approved_at=None)
                row = await db.fetchrow("SELECT * FROM script_video WHERE id=$1", vid)
            prod = row["product"]
            if prod is None:
                await _save(db, vid, stage="Finding the product")
                prod = await product(db, blobs, settings, row, work)
                await _save(db, vid, product=prod)
            cost = sd.cost(p)
            if row["approved_at"] is None:
                # Stop here with the exact cost; nothing is generated until a person approves it.
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
                    "Top it up, then make it again. Nothing was spent.",
                )
                return
            await _save(db, vid, stage="Generating the video")
            row = await db.fetchrow("SELECT * FROM script_video WHERE id=$1", vid)
            try:
                raw = await sd_generate(db, blobs, settings, row, p, prod, work)
            except Blocked as exc:
                await _save(db, vid, status="blocked", stage=None, error=f"AI Gateway refused the video call for credit: {exc}")
                return
            await _save(db, vid, stage="Checking the label" + (" and what was said" if sd.speaks(p["mode"]) else ""))
            final, notes = await sd_finish(db, settings, row, p, raw, work)
            path = f"{row['org_id']}/script_video/{vid}/final.mp4"
            await blobs.put(path, final.read_bytes(), "video/mp4")
            await _save(db, vid, stage="Checking it is new", path=path)
            p_chk = {"shots": [{"lines": [i]} for i in range(len(p["beats"]))]}
            chk = await checks(db, blobs, row, final, p_chk, [b["start"] for b in p["beats"]], work)
            await _save(db, vid, checks={**chk, **notes}, status="done", stage=None)
    except Exception as exc:  # noqa: BLE001 - shown on the page; paid steps are never re-sent
        log.exception("script video failed", extra={"script_video_id": str(vid)})
        await _save(db, vid, status="failed", stage=None, error=str(exc)[:1000])
