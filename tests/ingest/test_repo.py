from datetime import date, timedelta
from decimal import Decimal

import pytest

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo


@pytest.fixture
async def account_id(db, org_id, settings):
    return await repo.upsert_ad_account(
        db, org_id, "1", "Main", "USD", encrypt_str(settings, "tok"), None
    )


def _creative(cid="c1", fmt="image"):
    return PlatformCreative(
        cid, fmt, "H", "P", None, "SHOP_NOW", "https://x", "https://img", "image/png", {}
    )


async def test_upsert_creative_and_ad_and_metrics(db, org_id, account_id):
    cid, created = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert created
    cid2, created2 = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert cid2 == cid and not created2

    aid = await repo.upsert_ad(
        db, org_id, account_id, PlatformAd("ad1", "A", "ACTIVE", "as", "cp", "c1"), cid
    )
    written, skipped = await repo.upsert_metrics(
        db,
        org_id,
        account_id,
        [
            MetricRow(
                "ad1",
                date(2026, 9, 1),
                "facebook:feed",
                Decimal("10"),
                100,
                5,
                4,
                Decimal("1"),
                Decimal("50"),
                0,
                0,
                0,
                0,
                {},
            ),
            MetricRow(
                "ad1",
                date(2026, 9, 1),
                "instagram:reels",
                Decimal("5"),
                50,
                2,
                2,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            ),
            MetricRow(
                "unknown",
                date(2026, 9, 1),
                "facebook:feed",
                Decimal("1"),
                1,
                0,
                0,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            ),
        ],
    )
    assert written == 2 and skipped == 1
    days = await repo.creative_metrics(db, org_id, cid, date(2026, 8, 1), date(2026, 9, 30))
    assert len(days) == 1 and days[0]["spend"] == Decimal("15") and days[0]["impressions"] == 150
    assert aid is not None


async def test_assets_and_listing(db, org_id, account_id, blobs):
    cid, _ = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    assert not await repo.creative_has_asset(db, org_id, cid, "original_image")
    asset_id = await repo.insert_asset(
        db, org_id, cid, "original_image", "p/x.png", "sha", "image/png", 10, 10
    )
    await repo.set_primary_asset(db, org_id, cid, asset_id)
    assert await repo.creative_has_asset(db, org_id, cid, "original_image")
    rows = await repo.list_creatives(db, org_id)
    assert len(rows) == 1 and rows[0]["primary_asset_path"] == "p/x.png"
    assert (await repo.get_creative(db, org_id, cid))["headline"] == "H"


async def test_upsert_metrics_chunks_large_batches(db, org_id, account_id, monkeypatch):
    cid, _ = await repo.upsert_creative(db, org_id, account_id, _creative(), "raw/c1.json")
    await repo.upsert_ad(
        db, org_id, account_id, PlatformAd("ad1", "A", "ACTIVE", "as", "cp", "c1"), cid
    )
    monkeypatch.setattr(repo, "METRIC_CHUNK_SIZE", 10)
    rows = [
        MetricRow(
            "ad1",
            date(2026, 9, 1) + timedelta(days=i % 25),
            f"facebook:feed_{i}",
            Decimal("1"),
            i,
            0,
            0,
            Decimal("0"),
            Decimal("0"),
            0,
            0,
            0,
            0,
            {},
        )
        for i in range(25)
    ]
    written, skipped = await repo.upsert_metrics(db, org_id, account_id, rows)
    assert (written, skipped) == (25, 0)
    assert await db.fetchval("SELECT count(*) FROM metric_daily WHERE creative_id = $1", cid) == 25

    # re-running the same batch upserts in place rather than duplicating
    written, _ = await repo.upsert_metrics(db, org_id, account_id, rows)
    assert written == 25
    assert await db.fetchval("SELECT count(*) FROM metric_daily WHERE creative_id = $1", cid) == 25


async def test_upsert_metrics_with_no_known_ads_writes_nothing(db, org_id, account_id):
    written, skipped = await repo.upsert_metrics(
        db,
        org_id,
        account_id,
        [
            MetricRow(
                "ghost",
                date(2026, 9, 1),
                "facebook:feed",
                Decimal("1"),
                1,
                0,
                0,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            )
        ],
    )
    assert (written, skipped) == (0, 1)
