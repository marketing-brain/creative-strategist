from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy


async def seed_account(db, org_id, settings) -> UUID:
    aid = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "Main",
        "USD",
    )
    await ingest_repo.set_ad_account_status(db, org_id, aid, "active")
    return aid


async def seed_creative(
    db,
    org_id,
    account_id,
    name,
    fmt,
    tags: dict[str, str],
    daily: list[tuple[date, Decimal, int, int, Decimal, Decimal]],
) -> UUID:
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        account_id,
        PlatformCreative(name, fmt, f"H {name}", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    await ingest_repo.upsert_ad(
        db, org_id, account_id, PlatformAd(f"ad_{name}", name, "ACTIVE", "as", "cp", name), cid
    )
    tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await tag_repo.start_tag_run(db, org_id, cid, tv, "fake")
    await tag_repo.finish_tag_run(
        db,
        org_id,
        rid,
        [{"dimension": k, "value": v, "confidence": 0.9, "evidence": ""} for k, v in tags.items()],
        "s",
        1,
        1,
    )
    rows = [
        MetricRow(f"ad_{name}", d, "facebook:feed", sp, imp, clk, clk, conv, val, 0, 0, 0, 0, {})
        for d, sp, imp, clk, conv, val in daily
    ]
    await ingest_repo.upsert_metrics(db, org_id, account_id, rows)
    return cid
