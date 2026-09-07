from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_blobs, get_db
from cs.core import queue
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.generation import repo
from cs.generation.brand import get_active_brand_kit

router = APIRouter()


class NewGeneration(BaseModel):
    kind: str
    variants: int = 4
    source_insight_id: UUID | None = None
    source_creative_ids: list[UUID] = []
    concept: str | None = None


def _ser(r) -> dict:
    out = {}
    for k, v in dict(r).items():
        if isinstance(v, UUID):
            out[k] = str(v)
        elif isinstance(v, list):
            out[k] = [str(x) if isinstance(x, UUID) else x for x in v]
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.post("/generations")
async def create(
    body: NewGeneration, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    if body.kind not in ("static", "video"):
        raise HTTPException(400, "kind must be static or video")
    kit = await get_active_brand_kit(db, auth.org_id)
    if not kit:
        raise HTTPException(400, "create a brand kit first")
    if not body.concept and not body.source_insight_id:
        raise HTTPException(400, "provide a concept or a source insight")
    gid = await repo.create_generation(
        db,
        auth.org_id,
        kit.id,
        body.kind,
        max(1, min(body.variants, 8)),
        auth.user_id,
        body.source_insight_id,
        tuple(body.source_creative_ids),
        body.concept,
    )
    await queue.enqueue(
        db, auth.org_id, "generation.run", {"generation_id": str(gid)}, dedup_key=f"gen:{gid}"
    )
    return {"id": str(gid)}


@router.get("/generations")
async def list_gens(
    auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    return [_ser(r) for r in await repo.list_generations(db, auth.org_id)]


@router.get("/generations/{gid}")
async def get_gen(
    gid: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    g = await repo.get_generation(db, auth.org_id, gid)
    if not g:
        raise HTTPException(404)
    assets = []
    for a in await repo.list_generated_assets(db, auth.org_id, generation_id=gid):
        assets.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return {**_ser(g), "assets": assets}


@router.get("/review-queue")
async def review_queue(
    state: str = "draft",
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> list[dict]:
    out = []
    for a in await repo.list_generated_assets(db, auth.org_id, review_state=state):
        out.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return out


@router.post("/generated-assets/{aid}/approve")
async def approve(
    aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "approved", auth.user_id)}


@router.post("/generated-assets/{aid}/reject")
async def reject(
    aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "rejected", auth.user_id)}
