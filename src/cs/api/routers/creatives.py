from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/creatives")


# Postgres sums bigint columns into numeric; these are counts and must stay integers.
COUNT_FIELDS = ("impressions", "clicks", "link_clicks", "video_3s_views", "video_thruplay")


def _derived(r) -> dict:
    spend = float(r["spend"])
    impressions = int(r["impressions"])
    clicks = int(r["clicks"])
    conversions = float(r["conversions"])
    conversion_value = float(r["conversion_value"])
    return {
        **{
            k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
            for k, v in dict(r).items()
        },
        **{k: int(r[k] or 0) for k in COUNT_FIELDS if k in r},
        "day": r["day"].isoformat(),
        "ctr": round(clicks / impressions, 4) if impressions else 0.0,
        "cpa": round(spend / conversions, 2) if conversions else None,
        "roas": round(conversion_value / spend, 2) if spend else None,
    }


@router.get("")
async def list_creatives(
    account_id: UUID | None = None,
    format: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> list[dict]:
    rows = await repo.list_creatives(db, auth.org_id, account_id, format, limit, offset)
    out = []
    for r in rows:
        d = {**dict(r), "id": str(r["id"]), "ad_account_id": str(r["ad_account_id"])}
        d["primary_asset_url"] = (
            await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
        )
        out.append(d)
    return out


@router.get("/{creative_id}")
async def get_creative(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    r = await repo.get_creative(db, auth.org_id, creative_id)
    if not r:
        raise HTTPException(404)
    d = {k: (str(v) if isinstance(v, UUID) else v) for k, v in dict(r).items()}
    d["primary_asset_url"] = (
        await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
    )
    return d


@router.get("/{creative_id}/metrics")
async def creative_metrics(
    creative_id: UUID,
    since: date,
    until: date,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> list[dict]:
    if not await repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return [
        _derived(r) for r in await repo.creative_metrics(db, auth.org_id, creative_id, since, until)
    ]
