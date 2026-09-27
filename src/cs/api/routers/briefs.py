"""Variant briefs: pick a winning video, generate a brief for it, read and edit it."""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.briefs import facts
from cs.briefs import findings as findings_mod
from cs.briefs.jobs import JOB, RENDER_JOB
from cs.core import queue
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.ids import new_id

router = APIRouter(prefix="/briefs")
MAX_CONTENT_BYTES = 200_000
MAX_ORIGINAL_BYTES = 400_000_000
# An uploaded original may differ from the stored copy by this much and still be the same cut.
LENGTH_TOLERANCE_S = 0.35


async def _owned_account(db: Database, org_id: UUID, ad_account_id: UUID) -> None:
    if not await db.fetchval(
        "SELECT 1 FROM ad_account WHERE id=$1 AND org_id=$2", ad_account_id, org_id
    ):
        raise HTTPException(404, "ad account not found")


def _row(r, full: bool = False) -> dict:
    out: dict[str, Any] = {
        "id": str(r["id"]),
        "title": r["title"],
        "status": r["status"],
        "error": r["error"],
        "footage_id": str(r["footage_id"]),
        "parent_name": r["parent_name"],
        "version": r["version"],
        "focus": r["focus"],
        "created_at": r["created_at"].isoformat(),
        "updated_at": r["updated_at"].isoformat(),
    }
    if full:
        out["facts"] = r["facts"]
        out["content"] = r["content"]
    return out


_SELECT = """
    SELECT b.*, (SELECT ad.name FROM footage f JOIN ad ON ad.creative_id = f.rep_creative_id
                 WHERE f.id = b.footage_id ORDER BY ad.first_seen_at, ad.id LIMIT 1) AS parent_name
    FROM creative_brief b
"""


@router.get("")
async def list_briefs(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> list[dict]:
    await _owned_account(db, auth.org_id, ad_account_id)
    rows = await db.fetch(
        _SELECT + " WHERE b.org_id=$1 AND b.ad_account_id=$2 ORDER BY b.created_at DESC",
        auth.org_id,
        ad_account_id,
    )
    return [_row(r) for r in rows]


@router.get("/candidates")
async def candidates(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    await _owned_account(db, auth.org_id, ad_account_id)
    return {
        "rule": (
            f"Videos with ${facts.MIN_LIFETIME_SPEND}+ lifetime spend and {facts.MIN_PURCHASES_7D}+ purchases in their "
            "first 7 days, retargeting left out, cheapest first-week cost per purchase first."
        ),
        "candidates": await facts.candidates(db, auth.org_id, ad_account_id),
    }


class CreateBody(BaseModel):
    ad_account_id: UUID
    footage_id: UUID
    # A What works finding to build the variants around (cs.briefs.findings.finding_id).
    finding_id: str | None = None


@router.post("")
async def create_brief(
    body: CreateBody, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    await _owned_account(db, auth.org_id, body.ad_account_id)
    ok = await db.fetchval(
        "SELECT 1 FROM footage WHERE id=$1 AND org_id=$2 AND ad_account_id=$3 AND format='video'",
        body.footage_id,
        auth.org_id,
        body.ad_account_id,
    )
    if not ok:
        raise HTTPException(404, "video not found")
    focus = None
    if body.finding_id:
        focus = (await findings_mod.all_findings(db, auth.org_id)).get(body.finding_id)
        if focus is None:
            raise HTTPException(404, "finding not found")
    live = await db.fetchval(
        "SELECT id FROM creative_brief WHERE footage_id=$1 AND org_id=$2 AND status IN ('queued','running')",
        body.footage_id,
        auth.org_id,
    )
    if live:
        return {"id": str(live)}
    bid = new_id()
    await db.execute(
        "INSERT INTO creative_brief (id, org_id, ad_account_id, footage_id, title, status, created_by, focus)"
        " VALUES ($1, $2, $3, $4, 'Generating…', 'queued', $5, $6)",
        bid,
        auth.org_id,
        body.ad_account_id,
        body.footage_id,
        auth.user_id,
        focus,
    )
    # One attempt: the job makes a paid call, and a paid call is never retried.
    await queue.enqueue(
        db, auth.org_id, JOB, {"brief_id": str(bid)}, dedup_key=f"brief:{bid}", max_attempts=1
    )
    return {"id": str(bid)}


@router.get("/{brief_id}")
async def get_brief(
    brief_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    r = await db.fetchrow(_SELECT + " WHERE b.id=$1 AND b.org_id=$2", brief_id, auth.org_id)
    if r is None:
        raise HTTPException(404)
    out = _row(r, full=True)
    out["has_original"] = bool(r["original_path"])
    # Per variant: the newest attempt, except that a failed attempt never hides an earlier
    # finished render; its error rides along as retry_error.
    rows = await db.fetch(
        "SELECT id, code, status, edit, source, path, width, height, duration_s, error, created_at"
        " FROM brief_render WHERE brief_id=$1 ORDER BY code, created_at DESC",
        brief_id,
    )
    newest: dict[str, Any] = {}
    last_done: dict[str, Any] = {}
    for x in rows:
        newest.setdefault(x["code"], x)
        if x["status"] == "done":
            last_done.setdefault(x["code"], x)
    renders = []
    retry_error: dict[str, str | None] = {}
    for code, x in newest.items():
        if x["status"] == "failed" and code in last_done:
            retry_error[code] = x["error"]
            x = last_done[code]
        renders.append(x)
    out["renders"] = {
        x["code"]: {
            "id": str(x["id"]),
            "status": x["status"],
            "edit": x["edit"],
            "from_original": x["source"] == r["original_path"],
            "url": await blobs.url_for(x["path"]) if x["path"] else None,
            "path": x["path"],
            "width": x["width"],
            "height": x["height"],
            "duration_s": x["duration_s"],
            "error": x["error"],
            "retry_error": retry_error.get(x["code"]),
            "created_at": x["created_at"].isoformat(),
        }
        for x in renders
    }
    return out


class RenderBody(BaseModel):
    code: str


@router.post("/{brief_id}/render")
async def render_variant(
    brief_id: UUID,
    body: RenderBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Queue a render of one variant as the brief currently says it. Free: ffmpeg only."""
    b = await db.fetchrow(
        "SELECT content, facts->>'source_path' AS source_path, original_path FROM creative_brief"
        " WHERE id=$1 AND org_id=$2 AND status='done'",
        brief_id,
        auth.org_id,
    )
    if b is None:
        raise HTTPException(404)
    v = next(
        (
            v
            for bt in b["content"].get("batches", [])
            for v in bt["variants"]
            if v["code"] == body.code
        ),
        None,
    )
    if v is None or not v.get("edit"):
        raise HTTPException(
            422, "This variant needs new footage, so there is nothing to cut from the parent."
        )
    source = b["original_path"] or b["source_path"]
    if not source:
        raise HTTPException(422, "The parent has no stored video file.")
    segs = v["edit"].get("segments") or []
    if not segs or any(len(x) != 2 or not (0 <= float(x[0]) < float(x[1])) for x in segs):
        raise HTTPException(422, "The cuts are not valid: each piece needs a start before its end.")
    rid = new_id()
    await db.execute(
        "INSERT INTO brief_render (id, brief_id, org_id, code, status, edit, source)"
        " VALUES ($1, $2, $3, $4, 'queued', $5, $6)",
        rid,
        brief_id,
        auth.org_id,
        body.code,
        v["edit"],
        source,
    )
    await queue.enqueue(
        db,
        auth.org_id,
        RENDER_JOB,
        {"render_id": str(rid)},
        dedup_key=f"render:{rid}",
        max_attempts=1,
    )
    return {"id": str(rid)}


def _duration(data: bytes) -> float | None:
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "in.mp4"
        p.write_bytes(data)
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(p),
            ],
            capture_output=True,
            text=True,
        ).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


@router.post("/{brief_id}/original")
async def upload_original(
    brief_id: UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """A higher-quality copy of the parent, sent as the raw request body. Refused when its length
    differs from the ad's, because every cut time was measured on the ad's own copy."""
    b = await db.fetchrow(
        "SELECT (facts->>'duration_s')::float AS dur FROM creative_brief WHERE id=$1 AND org_id=$2",
        brief_id,
        auth.org_id,
    )
    if b is None:
        raise HTTPException(404)
    data = await request.body()
    if not data:
        raise HTTPException(400, "No file received.")
    if len(data) > MAX_ORIGINAL_BYTES:
        raise HTTPException(413, "The file is over 400 MB.")
    dur = await asyncio.to_thread(_duration, data)
    if dur is None:
        raise HTTPException(422, "That file is not a video ffmpeg can read.")
    if b["dur"] and abs(dur - b["dur"]) > LENGTH_TOLERANCE_S:
        raise HTTPException(
            422,
            f"This file is {dur:.1f}s and the ad is {b['dur']:.1f}s. The cuts were measured on the ad, "
            "so the file must be the same cut. Export the version that ran.",
        )
    path = f"{auth.org_id}/briefs/{brief_id}/original.mp4"
    await blobs.put(path, data, "video/mp4")
    await db.execute(
        "UPDATE creative_brief SET original_path=$2, updated_at=now() WHERE id=$1", brief_id, path
    )
    return {"duration_s": round(dur, 2)}


class UpdateBody(BaseModel):
    version: int
    title: str
    content: dict


@router.put("/{brief_id}")
async def update_brief(
    brief_id: UUID,
    body: UpdateBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    if len(json.dumps(body.content)) > MAX_CONTENT_BYTES:
        raise HTTPException(413, "brief too large")
    title = body.title.strip()[:200] or "Untitled brief"
    r = await db.fetchrow(
        """
        UPDATE creative_brief SET content=$4, title=$5, version=version+1, updated_at=now()
        WHERE id=$1 AND org_id=$2 AND version=$3 AND status='done'
        RETURNING version
        """,
        brief_id,
        auth.org_id,
        body.version,
        body.content,
        title,
    )
    if r is None:
        exists = await db.fetchval(
            "SELECT version FROM creative_brief WHERE id=$1 AND org_id=$2", brief_id, auth.org_id
        )
        if exists is None:
            raise HTTPException(404)
        raise HTTPException(
            409, "Someone saved this brief after you opened it. Reload to see their changes."
        )
    return {"version": r["version"]}


@router.delete("/{brief_id}", status_code=204)
async def delete_brief(
    brief_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> None:
    await db.execute("DELETE FROM creative_brief WHERE id=$1 AND org_id=$2", brief_id, auth.org_id)
