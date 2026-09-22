import re
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.connectors.fivetran.connect import schema_for
from cs.core import repo
from cs.core.blobs import BlobStore
from cs.core.db import Database
from cs.core.metrics import derived_metrics
from cs.core.repo import rollups
from cs.insights import play_curve
from cs.insights.account_read import repo as account_read_repo
from cs.insights.diagnose_creative import WINDOW_DAYS, diagnose_creative
from cs.insights.diagnosis import bucket_label, length_bucket

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


SCOPE = Query("footage", pattern="^(footage|creative)$")


async def _until(db: Database, org_id: UUID, ad_account_id: UUID) -> date:
    """The day every window on the creative page ends on: the account's newest day with
    delivery, or today for an account that has none yet. Anchoring on the wall clock instead
    would spend the trailing days of each window on whatever the connector has not synced."""
    last = await repo.account_last_metric_day(db, org_id, ad_account_id)
    return last or datetime.now(UTC).date()


async def _scope_ids(db: Database, org_id: UUID, creative_id: UUID, scope: str) -> list[UUID]:
    """The creatives a detail endpoint reads: the whole footage group by default, or just
    the one creative when the caller asks for its own numbers (scope=creative)."""
    if scope == "creative":
        return [creative_id]
    return await repo.footage_member_ids(db, org_id, creative_id)


async def _campaign_names(db: Database, org_id: UUID, campaign_ids: set[str]) -> dict[str, str]:
    """Current campaign names from the org's Fivetran schema. Display only, so an org whose
    schema or table is missing gets ids without names rather than an error."""
    ids = [int(c) for c in campaign_ids if c and c.isdigit()]
    if not ids:
        return {}
    try:
        rows = await db.fetch(
            f'SELECT DISTINCT ON (id) id, name FROM "{schema_for(org_id)}".campaign_history '
            "WHERE id = ANY($1::bigint[]) ORDER BY id, updated_time DESC",
            ids,
        )
    except asyncpg.PostgresError:
        return {}
    return {str(r["id"]): r["name"] for r in rows if r["name"]}


def _totals(r) -> dict:
    """30-day totals and the rates derived from them, for one creative, ad or group."""
    return {
        "spend": float(r["spend"]),
        "impressions": int(r["impressions"]),
        "clicks": int(r["clicks"]),
        "purchases": int(r["purchases"]),
        **derived_metrics(
            r["spend"],
            int(r["impressions"]),
            int(r["clicks"]),
            r["conversions"],
            r["conversion_value"],
        ),
    }


_COPY_SUFFIX = re.compile(r"(\s*[–-]\s*Copy(\s*\d+)?)+\s*$", re.IGNORECASE)
_GRID_KEYS = ("spend", "impressions", "link_clicks", "purchases", "hook_rate", "link_ctr", "cpa")


def _grid_rates(r, prefix: str, suffix: str = "") -> dict:
    """The card's share of `_rates`, read from prefixed list columns."""
    full = _rates({k: r[f"{prefix}{k}{suffix}"] for k in repo.GRID_METRIC_COLS})
    return {k: full[k] for k in (*_GRID_KEYS, "roas")}


@router.get("")
async def list_creatives(
    account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query(
        "spend", pattern="^(recent|spend|impressions|purchases|roas|cpa|link_ctr|hook_rate|ctr)$"
    ),
    order: str = Query("desc", pattern="^(desc|asc)$"),
    group: str = Query("footage", pattern="^(footage|creative)$"),
    delivery: str = Query("active", pattern="^(active|all)$"),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The grid. Each item carries its 30-day totals and rates (`totals`) beside the same
    rates pooled over the rest of its account and format (`baseline`), the comparison the
    creative page makes. `total` counts what the delivery filter lets through; `total_all`
    and `total_active` let the page say how many the filter is hiding."""
    if bool(dimension) != bool(value):
        raise HTTPException(422, "dimension and value must be provided together")
    rows = await repo.list_creatives(
        db,
        auth.org_id,
        account_id,
        format,
        limit,
        offset,
        dimension,
        value,
        sort,
        order,
        group,
        delivery,
        q,
    )
    passthrough = (
        "platform_creative_id",
        "format",
        "headline",
        "primary_text",
        "cta_type",
        "link_url",
        "first_seen_at",
        "last_seen_at",
        "primary_asset_mime",
    )
    out = []
    for r in rows:
        d = {k: r[k] for k in passthrough}
        d["id"] = str(r["id"])
        d["ad_account_id"] = str(r["ad_account_id"])
        d["primary_asset_url"] = (
            await blobs.url_for(r["primary_asset_path"]) if r["primary_asset_path"] else None
        )
        # Meta's own thumbnail only stands in while we hold no file for the creative.
        poster = r["poster_path"]
        if poster and "/posters/meta/" in poster and r["primary_asset_path"]:
            poster = None
        d["poster_url"] = await blobs.url_for(poster) if poster else None
        # "X – Copy 2" is Ads Manager's name for a duplicate; the card names the footage.
        d["ad_name"] = _COPY_SUFFIX.sub("", r["ad_name"]) if r["ad_name"] else None
        d["totals"] = _grid_rates(r, "", "_30d")
        d["baseline"] = _grid_rates(r, "base_")
        d["spend_30d"] = d["totals"]["spend"]
        for k in ("creative_count", "ad_count", "copy_variants"):
            d[k] = int(r[k])
        out.append(d)
    total_all, total_active = await repo.creative_counts(
        db, auth.org_id, account_id, format, dimension, value, group, q
    )
    window = await rollups.rollup_window(db, auth.org_id, account_id)
    return {
        "items": out,
        "total": total_active if delivery == "active" else total_all,
        "total_all": total_all,
        "total_active": total_active,
        # The days every number on the grid covers, so the page can name them.
        "window": {"start": window[0].isoformat(), "end": window[1].isoformat()}
        if window
        else None,
    }


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


@router.get("/{creative_id}/footage")
async def creative_footage(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """The footage group this creative belongs to: every creative running the same file, the
    ads under them with their own 30-day totals, and the distinct copy the file runs with.
    A creative that shares its file with nothing is a group of one."""
    if not await repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    ids = await repo.footage_member_ids(db, auth.org_id, creative_id)
    members = await repo.footage_members(db, auth.org_id, ids)
    ads = await repo.footage_ads(db, auth.org_id, ids)
    campaigns = await _campaign_names(db, auth.org_id, {a["campaign_id"] for a in ads})
    variants: dict[tuple, dict] = {}
    for m in members:
        key = (m["headline"] or "", m["primary_text"] or "")
        v = variants.setdefault(
            key,
            {
                "headline": m["headline"],
                "primary_text": m["primary_text"],
                "creative_ids": [],
                "sums": {
                    k: 0
                    for k in (
                        "spend",
                        "impressions",
                        "clicks",
                        "purchases",
                        "conversions",
                        "conversion_value",
                    )
                },
            },
        )
        v["creative_ids"].append(str(m["id"]))
        for k in v["sums"]:
            v["sums"][k] += m[k]
    return {
        "creative_ids": [str(m["id"]) for m in members],
        "ads": [
            {
                "id": str(a["id"]),
                "creative_id": str(a["creative_id"]),
                "platform_ad_id": a["platform_ad_id"],
                "name": a["name"],
                "status": a["status"],
                "campaign_id": a["campaign_id"],
                "campaign_name": campaigns.get(a["campaign_id"] or ""),
                "adset_id": a["adset_id"],
                **_totals(a),
            }
            for a in ads
        ],
        "copy_variants": [
            {
                "headline": v["headline"],
                "primary_text": v["primary_text"],
                "creative_ids": v["creative_ids"],
                **_totals(v["sums"]),
            }
            for v in variants.values()
        ],
    }


def _rates(r) -> dict:
    """Totals and the chain of rates between them, from one window-sums row. The chain is the
    ad's unit economics in funnel order: what an impression costs, how many stop, how many
    click, how many of those buy, what a purchase costs and what it is worth. Each figure
    explains the next (CPA = CPM / (1000 x link CTR x click-to-purchase); ROAS = AOV / CPA),
    so a reader can see which step a cost gap comes from. CPA divides by conversions, as the
    diagnosis does, so the two never show different costs.

    Frequency is left out on purpose: reach is stored per day and does not add up across
    days, so impressions / summed reach sits near 1.0 for every ad and says nothing."""

    def div(n, d) -> float | None:
        return float(n) / float(d) if n is not None and d else None

    spend, imps = r["spend"] or 0, int(r["impressions"] or 0)
    clicks, purchases = int(r["link_clicks"] or 0), int(r["purchases"] or 0)
    return {
        "spend": float(spend),
        "impressions": imps,
        "link_clicks": clicks,
        "purchases": purchases,
        "cpm": div(spend * 1000, imps),
        "hook_rate": div(r["video_3s_views"], imps),
        "link_ctr": div(clicks, imps),
        "click_to_purchase": div(purchases, clicks),
        "cpa": div(spend, r["conversions"]),
        "aov": div(r["conversion_value"], purchases),
        "roas": div(r["conversion_value"], spend),
    }


@router.get("/{creative_id}/summary")
async def creative_summary(
    creative_id: UUID,
    scope: str = SCOPE,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    """The page's headline numbers for the diagnosis window, beside the same numbers pooled
    over every other creative of the format in the account. Read from the same sums the
    diagnosis reads, so the header and the panel below it cannot disagree."""
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    ids = await _scope_ids(db, auth.org_id, creative_id, scope)
    until = await _until(db, auth.org_id, c["ad_account_id"])
    since = until - timedelta(days=WINDOW_DAYS - 1)
    mine = await repo.creative_window_sums(db, auth.org_id, ids, since, until)
    base = await repo.baseline_window_sums(
        db, auth.org_id, c["ad_account_id"], c["format"], ids, since, until
    )
    return {
        "window_start": since.isoformat(),
        "window_end": until.isoformat(),
        "totals": _rates(mine),
        "baseline": _rates(base),
        "baseline_creatives": int(base["creatives"] or 0),
    }


@router.get("/{creative_id}/play-curve")
async def creative_play_curve(
    creative_id: UUID,
    scope: str = SCOPE,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict | None:
    """Share of plays still watching, second by second, over the diagnosis window, beside
    other videos of similar length. None when the file is not a video, the org has no curve
    report, or there are too few plays to draw; the page then keeps its quartile chart."""
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    if c["format"] != "video":
        return None
    ids = await _scope_ids(db, auth.org_id, creative_id, scope)
    until = await _until(db, auth.org_id, c["ad_account_id"])
    since = until - timedelta(days=WINDOW_DAYS - 1)
    duration_ms = c["primary_asset_duration_ms"]
    bucket = length_bucket(duration_ms)
    rows = await play_curve.play_curve(
        db, auth.org_id, c["ad_account_id"], ids, since, until, bucket
    )
    return play_curve.shape(
        rows,
        duration_ms / 1000 if duration_ms else None,
        f"other videos {bucket_label(bucket)}" if bucket else None,
    )


@router.get("/{creative_id}/patterns")
async def creative_patterns(
    creative_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> list[dict]:
    """Graded account-wide patterns this creative's file is part of. Account evidence shown
    beside the creative, never a measurement of the creative itself."""
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    if c["format"] != "video" or not c["primary_asset_sha256"]:
        return []
    # Ranking is across every metric, so a cut of 12 let one layer's traits (link CTR) crowd
    # out another's. 40 keeps every graded row this file has on org 3 (17), still kilobytes.
    return await account_read_repo.patterns_for_file(
        db, auth.org_id, c["ad_account_id"], c["primary_asset_sha256"], limit=40
    )


@router.get("/{creative_id}/metrics")
async def creative_metrics(
    creative_id: UUID,
    since: date,
    until: date,
    scope: str = SCOPE,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> list[dict]:
    if not await repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    ids = await _scope_ids(db, auth.org_id, creative_id, scope)
    return [_derived(r) for r in await repo.creative_metrics(db, auth.org_id, ids, since, until)]


@router.get("/{creative_id}/diagnosis")
async def creative_diagnosis(
    creative_id: UUID,
    scope: str = SCOPE,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    c = await repo.get_creative(db, auth.org_id, creative_id)
    if not c:
        raise HTTPException(404)
    ids = await _scope_ids(db, auth.org_id, creative_id, scope)
    diagnosed = await diagnose_creative(
        db, auth.org_id, c, await _until(db, auth.org_id, c["ad_account_id"]), member_ids=ids
    )
    return diagnosed.diagnosis.to_dict()
