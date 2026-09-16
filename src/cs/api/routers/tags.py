from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.db import Database
from cs.tagging import repo, vocab
from cs.tagging.taxonomy import load_taxonomy

router = APIRouter()


class BackfillBody(BaseModel):
    limit: int = Field(500, ge=1, le=5000)


@router.get("/creatives/{creative_id}/tags")
async def creative_tags(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return [
        {**dict(r), "confidence": round(float(r["confidence"]), 4)}
        for r in await repo.latest_tags(db, auth.org_id, creative_id)
        if r["dimension"] != "transcript"
    ]


@router.get("/creatives/{creative_id}/transcript")
async def creative_transcript(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return {"transcript": await repo.latest_tag_value(db, auth.org_id, creative_id, "transcript")}


TAGGABLE_FORMATS = ("image", "video", "carousel")


@router.post("/creatives/{creative_id}/retag")
async def retag(
    creative_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    creative = await ingest_repo.get_creative(db, auth.org_id, creative_id)
    if not creative:
        raise HTTPException(404)
    if creative["format"] not in TAGGABLE_FORMATS:
        # 'unresolved' creatives have no proven format: taxonomy.for_format('unresolved')
        # returns [], which turns into a JSON schema enum of [] -- a request structured-
        # output APIs reject outright. Never enqueue a job doomed to fail before it starts.
        raise HTTPException(422, f"cannot tag a creative with format '{creative['format']}'")
    jid = await queue.enqueue(
        db,
        auth.org_id,
        "tagging.tag_creative",
        {"creative_id": str(creative_id)},
        dedup_key=f"tag:{creative_id}",
    )
    return {"job_id": str(jid) if jid else None}


@router.get("/tags/dimensions")
async def dimensions(auth: AuthContext = Depends(current_auth)) -> list[dict]:
    t = load_taxonomy()
    return [
        {
            "name": d.name,
            "group": d.group,
            "applies_to": list(d.applies_to),
            "values": list(d.values) if d.values else None,
            "description": d.description,
            "kind": d.kind,
        }
        for d in t.dimensions
    ]


@router.get("/tags/dimensions/{name}/values")
async def dimension_values(
    name: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    dim = next((d for d in load_taxonomy().dimensions if d.name == name), None)
    if dim is None or dim.kind != "open":
        raise HTTPException(404, "not an open dimension")
    return [
        {"value": slug, "count": n} for slug, n in await vocab.hints(db, auth.org_id, name, 200)
    ]


@router.post("/tagging/backfill")
async def backfill(
    body: BackfillBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    jid = await queue.enqueue(
        db, auth.org_id, "tagging.batch_submit", {"limit": body.limit}, dedup_key="batch_submit"
    )
    return {"job_id": str(jid) if jid else None}
