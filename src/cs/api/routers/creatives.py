from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core import repo
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.metrics import derived_metrics
from cs.insights import repo as insights_repo
from cs.insights.diagnose_creative import diagnose_creative

router = APIRouter(prefix="/creatives")


# Postgres sums bigint columns into numeric; these are counts and must stay integers.
COUNT_FIELDS = ("impressions", "clicks", "link_clicks", "video_3s_views", "video_thruplay")


def _derived(r) -> dict:
    out = {
        **{
            k: (float(v) if hasattr(v, "as_integer_ratio") and not isinstance(v, int) else v)
            for k, v in dict(r).items()
        },
        **{k: int(r[k] or 0) for k in COUNT_FIELDS if k in r},
    }
    out["day"] = r["day"].isoformat()
    out.update(
        derived_metrics(
            r["spend"],
            int(r["impressions"]),
            int(r["clicks"]),
            r["conversions"],
            r["conversion_value"],
        )
    )
    return out


@router.get("")
async def list_creatives(
    account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("recent", pattern="^(recent|spend|impressions|ctr)$"),
    order: str = Query("desc", pattern="^(desc|asc)$"),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    if bool(dimension) != bool(value):
        raise HTTPException(422, "dimension and value must be provided together")
    rows = await repo.list_creatives(
        db, auth.org_id, account_id, format, limit, offset, dimension, value, sort, order
    )
    out = []
    for r in rows:
        d = {**dict(r), "id": str(r["id"]), "ad_account_id": str(r["ad_account_id"])}
        d["primary_asset_url"] = (
            await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
        )
        d["ctr_30d"] = derived_metrics(
            r["spend_30d"],
            int(r["impressions_30d"] or 0),
            int(r["clicks_30d"] or 0),
            r["conversions_30d"] or Decimal(0),
            r["conversion_value_30d"] or Decimal(0),
        )["ctr"]
        d["spend_30d"] = float(r["spend_30d"])
        for k in ("impressions_30d", "clicks_30d", "conversions_30d", "conversion_value_30d"):
            d.pop(k, None)
        out.append(d)
    total = await repo.count_creatives(db, auth.org_id, account_id, format, dimension, value)
    return {"items": out, "total": total}


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


@router.get("/{creative_id}/diagnosis")
async def creative_diagnosis(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    diagnosed = await diagnose_creative(db, auth.org_id, c, datetime.now(UTC).date())
    return diagnosed.diagnosis.to_dict()


@router.get("/{creative_id}/insight")
async def creative_insight(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """The creative's written insight, or {"paragraph": null} until one exists. Rejected
    drafts (numbers or timestamps not in the brief) are never served."""
    if not await repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    row = await insights_repo.get_creative_insight(db, auth.org_id, creative_id)
    if row is None or row["status"] != "done":
        return {"paragraph": None}
    return {
        "paragraph": row["paragraph"],
        "window_start": row["window_start"].isoformat(),
        "window_end": row["window_end"].isoformat(),
        "created_at": row["created_at"].isoformat(),
    }
