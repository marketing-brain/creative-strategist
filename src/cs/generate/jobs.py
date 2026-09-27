"""generate.opening_edit: one paid video edit, then free fitting, stretching and joining."""

from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

from cs.core import model_calls
from cs.generate import gateway_edit as gw
from cs.generate import opening
from cs.generate import wavespeed as ws
from cs.jobs.registry import JobContext, handler

JOB = "generate.opening_edit"
PURPOSE = "generate.opening_edit"
log = logging.getLogger(__name__)


async def _stage(ctx: JobContext, gid: UUID, stage: str, **cols) -> None:
    sets = ", ".join(f"{k}=${i}" for i, k in enumerate(cols, 3))
    await ctx.db.execute(
        f"UPDATE generation SET stage=$2{', ' + sets if sets else ''} WHERE id=$1",
        gid,
        stage,
        *cols.values(),
    )


@handler(JOB)
async def opening_edit(ctx: JobContext) -> None:
    gid = UUID(ctx.job.payload["generation_id"])
    g = await ctx.db.fetchrow("SELECT * FROM generation WHERE id=$1", gid)
    if g is None or g["status"] != "queued":
        return
    await ctx.db.execute(
        "UPDATE generation SET status='running', stage='Preparing the clip' WHERE id=$1", gid
    )
    try:
        path = await ctx.db.fetchval(
            "SELECT a.path FROM footage f JOIN creative c ON c.id = f.rep_creative_id"
            " JOIN asset a ON a.id = c.primary_asset_id WHERE f.id=$1",
            g["footage_id"],
        )
        original = await ctx.blobs.get(path)
        cuts = await asyncio.to_thread(_cuts, original)
        clip = await asyncio.to_thread(_clip, original, g["window_s"])
        clip_key = f"{g['org_id']}/generate/{gid}/input.mp4"
        await ctx.blobs.put(clip_key, clip, "video/mp4")
        url = await ctx.blobs.url_for(clip_key, expires_seconds=7200)

        key = f"{gid}"
        if await model_calls.attempts_for(ctx.db, PURPOSE, key):
            raise RuntimeError("this generation was already sent once; paid calls are not re-sent")
        await _stage(ctx, gid, "Editing with Seedance 2.5 (usually 3–6 minutes)")
        width, height = await asyncio.to_thread(_size, clip)
        claim = await model_calls.claim(ctx.db, g["org_id"], PURPOSE, key, g["model"])
        try:
            op = await gw.submit(
                ctx.settings,
                url,
                ws.edit_prompt(g["prompt"], "[Video 1]"),
                g["resolution"],
                width,
                height,
                f"generate:{gid}",
            )
            await _stage(
                ctx, gid, "Editing with Seedance 2.5 (usually 3–6 minutes)", prediction_id=json.dumps(op)
            )
            edited = await gw.wait(ctx.settings, op)
        except Exception as exc:
            await model_calls.fail(ctx.db, claim.id, str(exc))
            raise
        await model_calls.finish(ctx.db, claim.id)
        raw_key = f"{g['org_id']}/generate/{gid}/edit.mp4"
        await ctx.blobs.put(raw_key, edited, "video/mp4")

        await _stage(ctx, gid, "Matching timing and joining to the original", raw_path=raw_key)
        cut = next((c for c in cuts if 3.0 <= c <= g["window_s"] - 0.3), None)
        r = await asyncio.to_thread(opening.finish, original, edited, g["window_s"], cut)
        res_key = f"{g['org_id']}/generate/{gid}/result.mp4"
        cmp_key = f"{g['org_id']}/generate/{gid}/compare.mp4"
        await ctx.blobs.put(res_key, r["result"], "video/mp4")
        await ctx.blobs.put(cmp_key, r["compare"], "video/mp4")
    except Exception as exc:  # noqa: BLE001 - shown on the page; the paid call is not re-sent
        log.exception("generation failed", extra={"generation_id": str(gid)})
        await ctx.db.execute(
            "UPDATE generation SET status='failed', stage=NULL, error=$2, finished_at=now() WHERE id=$1",
            gid,
            str(exc)[:1000],
        )
        return
    await ctx.db.execute(
        "UPDATE generation SET status='done', stage=NULL, speed=$2, in_sync_until_s=$3, swap_at_s=$4, result_path=$5,"
        " compare_path=$6, notes=$7, finished_at=now() WHERE id=$1",
        gid,
        r["speed"],
        r["in_sync_until_s"],
        r["swap_at_s"],
        res_key,
        cmp_key,
        r["notes"],
    )


def _cuts(original: bytes) -> list[float]:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "o.mp4"
        p.write_bytes(original)
        return opening.shot_changes(p)


def _size(clip: bytes) -> tuple[int, int]:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "c.mp4"
        p.write_bytes(clip)
        return opening.size(p)


def _clip(original: bytes, seconds: float) -> bytes:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "o.mp4", Path(tmp) / "c.mp4"
        src.write_bytes(original)
        opening.cut_clip(src, out, seconds)
        return out.read_bytes()
