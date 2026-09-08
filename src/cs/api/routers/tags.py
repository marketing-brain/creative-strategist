from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core.db import Database
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
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
    ]


@router.post("/creatives/{creative_id}/retag")
async def retag(
    creative_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
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
        }
        for d in t.dimensions
    ]


@router.post("/tagging/backfill")
async def backfill(
    body: BackfillBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    jid = await queue.enqueue(
        db, auth.org_id, "tagging.batch_submit", {"limit": body.limit}, dedup_key="batch_submit"
    )
    return {"job_id": str(jid) if jid else None}
