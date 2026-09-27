"""Generate: new openings for existing videos, edited from a prompt by a video model."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_blobs, get_db, get_settings_dep
from cs.core import queue
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.generate import opening
from cs.generate.jobs import JOB
from cs.generate.wavespeed import EDIT_MODEL

router = APIRouter(prefix="/generate")
WEAK_HOOK = 0.8  # hook rate at or below 80% of the account's counts as a weak opening


async def _owned_account(db: Database, org_id: UUID, ad_account_id: UUID) -> None:
    if not await db.fetchval(
        "SELECT 1 FROM ad_account WHERE id=$1 AND org_id=$2", ad_account_id, org_id
    ):
        raise HTTPException(404, "ad account not found")


@router.get("/candidates")
async def candidates(
    ad_account_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    """Videos by last-30-day spend with their hook rate against the account's. Converting
    videos with a weak opening are marked: a better opening moves the most money there."""
    await _owned_account(db, auth.org_id, ad_account_id)
    rows = await db.fetch(
        """
        WITH w AS (
          SELECT f.id, f.duration_s, m.spend, m.impressions, m.views_3s, m.purchases,
                 (SELECT ad.name FROM ad WHERE ad.creative_id = f.rep_creative_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS name
          FROM footage f JOIN footage_metric_window m ON m.footage_id = f.id AND m.window_kind = 'last_30d'
          WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = 'video' AND m.impressions >= 1000
        ), b AS (
          SELECT sum(views_3s)::float / nullif(sum(impressions), 0) AS hook,
                 sum(spend)::float / nullif(sum(purchases), 0) AS cpa FROM w
        )
        SELECT w.*, b.hook AS account_hook, b.cpa AS account_cpa FROM w, b ORDER BY w.spend DESC LIMIT 60
        """,
        auth.org_id,
        ad_account_id,
    )
    out = []
    for r in rows:
        hook = r["views_3s"] / r["impressions"] if r["impressions"] else None
        cpa = float(r["spend"]) / r["purchases"] if r["purchases"] else None
        acct_hook, acct_cpa = r["account_hook"], r["account_cpa"]
        out.append(
            {
                "footage_id": str(r["id"]),
                "name": r["name"] or "(unnamed)",
                "duration_s": float(r["duration_s"] or 0),
                "spend_30d": round(float(r["spend"]), 2),
                "purchases_30d": int(r["purchases"]),
                "cpa_30d": round(cpa, 2) if cpa else None,
                "hook_rate": round(hook, 4) if hook is not None else None,
                "hook_vs_account": round(hook / acct_hook - 1, 3)
                if hook is not None and acct_hook
                else None,
                "weak_hook": bool(hook is not None and acct_hook and hook <= acct_hook * WEAK_HOOK),
                "converting": bool(cpa and acct_cpa and cpa <= acct_cpa and r["purchases"] >= 5),
            }
        )
    first = rows[0] if rows else None
    return {
        "account_hook_rate": round(first["account_hook"], 4)
        if first and first["account_hook"]
        else None,
        "account_cpa": round(first["account_cpa"], 2) if first and first["account_cpa"] else None,
        "candidates": out,
    }


async def _plan(db: Database, blobs: BlobStore, org_id: UUID, footage_id: UUID) -> dict:
    path = await db.fetchval(
        "SELECT a.path FROM footage f JOIN creative c ON c.id = f.rep_creative_id"
        " JOIN asset a ON a.id = c.primary_asset_id WHERE f.id=$1 AND f.org_id=$2 AND f.format='video'",
        footage_id,
        org_id,
    )
    if not path:
        raise HTTPException(404, "video not found")
    data = await blobs.get(path)

    def measure() -> tuple[list[float], float, tuple[int, int]]:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "v.mp4"
            p.write_bytes(data)
            return opening.shot_changes(p), opening.duration(p), opening.size(p)

    cuts, total, (w, h) = await asyncio.to_thread(measure)
    window, cut = opening.plan_window(cuts, total)
    return {
        "window_s": window,
        "cut_s": cut,
        "width": w,
        "height": h,
        "cost": {res: opening.estimate_cost(window, res) for res in opening.RATE},
    }


@router.get("/plan")
async def plan(
    footage_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """How many seconds would be sent, where the join would land, and what each size costs."""
    return await _plan(db, blobs, auth.org_id, footage_id)


class CreateBody(BaseModel):
    ad_account_id: UUID
    footage_id: UUID
    prompt: str = Field(min_length=10, max_length=2000)
    resolution: str = "720p"


@router.post("")
async def create(
    body: CreateBody,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Queue one paid edit. The price is fixed here from the measured window."""
    if body.resolution not in opening.RATE:
        raise HTTPException(422, "resolution must be 480p, 720p or 1080p")
    if not settings.wavespeed_api_key:
        raise HTTPException(
            503,
            "Video generation is not set up: add WAVESPEED_API_KEY to the server's environment.",
        )
    await _owned_account(db, auth.org_id, body.ad_account_id)
    p = await _plan(db, blobs, auth.org_id, body.footage_id)
    gid = new_id()
    await db.execute(
        "INSERT INTO generation (id, org_id, ad_account_id, footage_id, prompt, model, resolution, window_s, status,"
        " stage, estimated_cost_usd, created_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'queued','Waiting to start',$9,$10)",
        gid,
        auth.org_id,
        body.ad_account_id,
        body.footage_id,
        body.prompt.strip(),
        f"wavespeed/{EDIT_MODEL}",
        body.resolution,
        p["window_s"],
        p["cost"][body.resolution],
        auth.user_id,
    )
    # One attempt: the job makes a paid call, and a paid call is never re-sent.
    await queue.enqueue(
        db, auth.org_id, JOB, {"generation_id": str(gid)}, dedup_key=f"gen:{gid}", max_attempts=1
    )
    return {"id": str(gid)}


_SELECT = """
    SELECT g.*, (SELECT ad.name FROM footage f JOIN ad ON ad.creative_id = f.rep_creative_id
                 WHERE f.id = g.footage_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS parent_name,
           (SELECT f.rep_creative_id FROM footage f WHERE f.id = g.footage_id) AS rep_creative_id
    FROM generation g
"""


async def _out(r, blobs: BlobStore, full: bool) -> dict:
    async def url(p: str | None) -> str | None:
        return await blobs.url_for(p) if p else None

    out: dict[str, Any] = {
        "id": str(r["id"]),
        "footage_id": str(r["footage_id"]),
        "rep_creative_id": str(r["rep_creative_id"]) if r["rep_creative_id"] else None,
        "parent_name": r["parent_name"],
        "prompt": r["prompt"],
        "model": r["model"],
        "resolution": r["resolution"],
        "window_s": r["window_s"],
        "status": r["status"],
        "stage": r["stage"],
        "estimated_cost_usd": float(r["estimated_cost_usd"])
        if r["estimated_cost_usd"] is not None
        else None,
        "error": r["error"],
        "created_at": r["created_at"].isoformat(),
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        "compare_url": await url(r["compare_path"]),
    }
    if full:
        out.update(
            {
                "speed": r["speed"],
                "in_sync_until_s": r["in_sync_until_s"],
                "swap_at_s": r["swap_at_s"],
                "notes": r["notes"],
                "result_url": await url(r["result_path"]),
                "raw_url": await url(r["raw_path"]),
            }
        )
    return out


@router.get("")
async def list_generations(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> list[dict]:
    await _owned_account(db, auth.org_id, ad_account_id)
    rows = await db.fetch(
        _SELECT + " WHERE g.org_id=$1 AND g.ad_account_id=$2 ORDER BY g.created_at DESC",
        auth.org_id,
        ad_account_id,
    )
    return [await _out(r, blobs, False) for r in rows]


@router.get("/{generation_id}")
async def get_generation(
    generation_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    r = await db.fetchrow(_SELECT + " WHERE g.id=$1 AND g.org_id=$2", generation_id, auth.org_id)
    if r is None:
        raise HTTPException(404)
    return await _out(r, blobs, True)


@router.delete("/{generation_id}", status_code=204)
async def delete_generation(
    generation_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> None:
    await db.execute("DELETE FROM generation WHERE id=$1 AND org_id=$2", generation_id, auth.org_id)
