from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core import repo
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.metrics import derived_metrics
from cs.insights.diagnosis import FATIGUE_MIN_DAYS, Sums, diagnose

router = APIRouter(prefix="/creatives")

# Diagnosis window: trailing 30 days ending today (UTC), matching the library's 30d columns.
WINDOW_DAYS = 30


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
    sort: Literal["spend", "recent"] = "spend",
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    if bool(dimension) != bool(value):
        raise HTTPException(422, "dimension and value must be provided together")
    rows = await repo.list_creatives(
        db, auth.org_id, account_id, format, limit, offset, dimension, value, sort
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
    until = datetime.now(UTC).date()
    since = until - timedelta(days=WINDOW_DAYS - 1)
    window = Sums.from_record(
        await repo.creative_window_sums(db, auth.org_id, creative_id, since, until)
    )
    base = await repo.baseline_window_sums(
        db, auth.org_id, c["ad_account_id"], c["format"], creative_id, since, until
    )
    first_day = await repo.creative_first_metric_day(db, auth.org_id, creative_id)
    days_live = (until - first_day).days + 1 if first_day else None
    first14 = trailing7 = None
    if first_day is not None and days_live is not None and days_live >= FATIGUE_MIN_DAYS:
        first14 = Sums.from_record(
            await repo.creative_window_sums(
                db, auth.org_id, creative_id, first_day, first_day + timedelta(days=13)
            )
        )
        trailing7 = Sums.from_record(
            await repo.creative_window_sums(
                db, auth.org_id, creative_id, until - timedelta(days=6), until
            )
        )
    return diagnose(
        fmt=c["format"],
        window=window,
        baseline=Sums.from_record(base),
        baseline_creatives=int(base["creatives"] or 0),
        first14=first14,
        trailing7=trailing7,
        days_live=days_live,
        duration_ms=c["primary_asset_duration_ms"],
        window_start=since,
        window_end=until,
    ).to_dict()
