from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
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
async def create(body: NewGeneration, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    if body.kind not in ("static", "video"):
        raise HTTPException(400, "kind must be static or video")
    kit = await get_active_brand_kit(db, auth.org_id)
    if not kit:
        raise HTTPException(400, "create a brand kit first")
    if not body.concept and not body.source_insight_id:
        raise HTTPException(400, "provide a concept or a source insight")
    gid = await repo.create_generation(db, auth.org_id, kit.id, body.kind, max(1, min(body.variants, 8)), auth.user_id,
                                       body.source_insight_id, tuple(body.source_creative_ids), body.concept)
    await queue.enqueue(db, auth.org_id, "generation.run", {"generation_id": str(gid)}, dedup_key=f"gen:{gid}")
    return {"id": str(gid)}


@router.get("/generations")
async def list_gens(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_generations(db, auth.org_id)]


@router.get("/generations/{gid}")
async def get_gen(gid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                  blobs: BlobStore = Depends(get_blobs)) -> dict:
    g = await repo.get_generation(db, auth.org_id, gid)
    if not g:
        raise HTTPException(404)
    assets = []
    for a in await repo.list_generated_assets(db, auth.org_id, generation_id=gid):
        assets.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return {**_ser(g), "assets": assets}


class PublishBody(BaseModel):
    ad_account_id: UUID
    campaign_id: str
    adset_id: str
    page_id: str
    link_url: str
    headline: str | None = None
    primary_text: str | None = None
    cta_type: str | None = None
    status: str = "PAUSED"


@router.get("/review-queue")
async def review_queue(state: str = "draft", auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                       blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    out = []
    for a in await repo.list_generated_assets(db, auth.org_id, review_state=state):
        out.append({**_ser(a), "url": await blobs.url_for(a["path"])})
    return out


@router.post("/generated-assets/{aid}/approve")
async def approve(aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "approved", auth.user_id)}


@router.post("/generated-assets/{aid}/reject")
async def reject(aid: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    return {"ok": await repo.set_review_state(db, auth.org_id, aid, "rejected", auth.user_id)}


@router.post("/generated-assets/{aid}/publish")
async def publish(aid: UUID, body: PublishBody, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    from cs.core.ids import new_id

    a = await repo.get_generated_asset(db, auth.org_id, aid)
    if not a:
        raise HTTPException(404)
    if a["review_state"] != "approved":
        raise HTTPException(409, "asset must be approved before publishing")
    if body.status not in ("PAUSED", "ACTIVE"):
        raise HTTPException(400, "status must be PAUSED or ACTIVE")
    pj = new_id()
    form = body.model_dump(mode="json")
    await db.execute(
        "INSERT INTO publish_job (id, org_id, generated_asset_id, ad_account_id, campaign_id, adset_id, ad_status_requested, form, status) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending')", pj, auth.org_id, aid, body.ad_account_id, body.campaign_id, body.adset_id, body.status, form)
    await queue.enqueue(db, auth.org_id, "publish.meta", {"publish_job_id": str(pj)}, dedup_key=f"publish:{pj}")
    return {"publish_job_id": str(pj)}


@router.get("/publish-jobs")
async def publish_jobs(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    rows = await db.fetch("SELECT * FROM publish_job WHERE org_id=$1 ORDER BY created_at DESC LIMIT 100", auth.org_id)
    return [_ser(r) for r in rows]
