from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.api.routers.creatives import _rates
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.repo import library as repo
from cs.core.repo import rollups

router = APIRouter(prefix="/library")

# The rates a Library row or card shows; the rest of `_rates` stays on the creative page.
_KEYS = ("spend", "impressions", "link_clicks", "purchases", "hook_rate", "link_ctr", "cpa", "roas")


def _card_rates(r, prefix: str = "") -> dict:
    full = _rates({k: r[f"{prefix}{k}"] for k in repo.METRIC_COLS})
    return {k: full[k] for k in _KEYS}


def _add(into: dict, r) -> None:
    for k in repo.METRIC_COLS:
        into[k] = into.get(k, 0) + (r[k] or 0)


@router.get("/tree")
async def library_tree(
    account_id: UUID | None = None,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """Campaigns, each with its ad sets, every node carrying its 30-day totals and how many
    of its ads are live, spent, or exist at all. Sorted by spend, the order a buyer reads
    Ads Manager in."""
    rows = await repo.tree(db, auth.org_id, account_id)
    campaigns: dict[str, dict] = {}
    account: dict = {}
    for r in rows:
        _add(account, r)
        c = campaigns.setdefault(
            r["campaign_id"],
            {
                "id": r["campaign_id"] or None,
                "name": r["campaign_name"],
                "ad_account_id": str(r["ad_account_id"]),
                "ads": 0,
                "ads_live": 0,
                "ads_spent": 0,
                "sums": {},
                "adsets": [],
            },
        )
        for k in ("ads", "ads_live", "ads_spent"):
            c[k] += int(r[k])
        _add(c["sums"], r)
        c["adsets"].append(
            {
                "id": r["adset_id"] or None,
                "name": r["adset_name"],
                "ads": int(r["ads"]),
                "ads_live": int(r["ads_live"]),
                "ads_spent": int(r["ads_spent"]),
                "totals": _card_rates(r),
            }
        )
    out = []
    for c in campaigns.values():
        c["totals"] = _card_rates(c.pop("sums"))
        c["adsets"].sort(key=lambda s: (-s["totals"]["spend"], s["name"] or ""))
        out.append(c)
    out.sort(key=lambda c: (-c["totals"]["spend"], c["name"] or ""))
    window = await rollups.rollup_window(db, auth.org_id, account_id)
    return {
        "campaigns": out,
        "totals": _card_rates(account) if account else None,
        "window": {"start": window[0].isoformat(), "end": window[1].isoformat()}
        if window
        else None,
    }


@router.get("/ads")
async def library_ads(
    account_id: UUID | None = None,
    campaign_id: str | None = Query(None, max_length=40),
    adset_id: str | None = Query(None, max_length=40),
    format: str | None = Query(None, pattern="^(image|video|carousel|unresolved)$"),
    q: str | None = Query(None, max_length=200),
    delivery: str = Query("spent", pattern="^(spent|live|all)$"),
    sort: str = Query(
        "spend", pattern="^(recent|spend|impressions|purchases|roas|cpa|link_ctr|hook_rate)$"
    ),
    order: str = Query("desc", pattern="^(desc|asc)$"),
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The ads under one node of the tree (or the whole account), one card per ad with the
    ad's own totals and, as `baseline`, the other ads of its format under the same node."""
    rows = await repo.list_ads(
        db,
        auth.org_id,
        account_id,
        campaign_id,
        adset_id,
        format,
        q,
        delivery,
        sort,
        order,
        limit,
        offset,
    )
    items = []
    for r in rows:
        poster = r["poster_path"]
        # Meta's own thumbnail only stands in while we hold no file for the creative.
        if poster and "/posters/meta/" in poster and r["primary_asset_path"]:
            poster = None
        items.append(
            {
                "id": str(r["id"]),
                "ad_account_id": str(r["ad_account_id"]),
                "platform_ad_id": r["platform_ad_id"],
                "name": r["name"],
                "status": r["status"],
                "effective_status": r["effective_status"],
                "campaign_id": r["campaign_id"] or None,
                "campaign_name": r["campaign_name"],
                "adset_id": r["adset_id"] or None,
                "adset_name": r["adset_name"],
                "creative_id": str(r["creative_id"]),
                "format": r["format"],
                "headline": r["headline"],
                "primary_text": r["primary_text"],
                "primary_asset_url": await blobs.url_for(r["primary_asset_path"])
                if r["primary_asset_path"]
                else None,
                "primary_asset_mime": r["primary_asset_mime"],
                "poster_url": await blobs.url_for(poster) if poster else None,
                "totals": _card_rates(r),
                "baseline": _card_rates(r, "base_"),
            }
        )
    counts = await repo.count_ads(
        db, auth.org_id, account_id, campaign_id, adset_id, format, q
    )
    return {"items": items, "total": counts[delivery], "counts": counts}
