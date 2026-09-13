from datetime import date, timedelta
from decimal import Decimal

import pytest

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo


@pytest.fixture
async def account_id(db, org_id, settings):
    return await repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "Main",
        "USD",
    )


def _creative(cid="c1", fmt="image"):
    return PlatformCreative(
        cid, fmt, "H", "P", None, "SHOP_NOW", "https://x", "https://img", "image/png", {}
    )


async def test_upsert_creative_and_ad_and_metrics(db, org_id, account_id):
    cid, created = await repo.upsert_creative(
        db, org_id, account_id, _creative(), "raw/c1.json", format_source="manual_review"
    )
    assert created
    cid2, created2 = await repo.upsert_creative(
        db, org_id, account_id, _creative(), "raw/c1.json", format_source="manual_review"
    )
    assert cid2 == cid and not created2


async def test_upsert_creative_refreshes_content_on_conflict(db, org_id, account_id):
    cid, _ = await repo.upsert_creative(
        db, org_id, account_id, _creative(), "raw/c1.json", format_source="manual_review"
    )
    edited = PlatformCreative(
        "c1",
        "image",
        "New headline",
        "New body",
        None,
        "LEARN_MORE",
        "https://y",
        "https://img",
        "image/png",
        {},
    )
    cid2, created2 = await repo.upsert_creative(
        db, org_id, account_id, edited, "raw/c1.json", format_source="manual_review"
    )
    assert cid2 == cid and not created2
    row = await db.fetchrow(
        "SELECT headline, primary_text, cta_type, link_url FROM creative WHERE id=$1", cid
    )
    assert row["headline"] == "New headline"
    assert row["primary_text"] == "New body"
    assert row["cta_type"] == "LEARN_MORE"
    assert row["link_url"] == "https://y"

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
    cid, _ = await repo.upsert_creative(
        db, org_id, account_id, _creative(), "raw/c1.json", format_source="manual_review"
    )
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
    cid, _ = await repo.upsert_creative(
        db, org_id, account_id, _creative(), "raw/c1.json", format_source="manual_review"
    )
    await repo.upsert_ad(
        db, org_id, account_id, PlatformAd("ad1", "A", "ACTIVE", "as", "cp", "c1"), cid
    )
    monkeypatch.setattr(repo.metrics, "METRIC_CHUNK_SIZE", 10)
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


async def test_list_assets_by_kind_orders_by_seq(db, org_id, settings):
    acct = await repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cx", "carousel", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    for i in (2, 0, 1):  # insert out of order
        await repo.insert_asset(
            db, org_id, cid, "original_image", f"o/{i}.png", "sha", "image/png", seq=i
        )
    rows = await repo.list_assets_by_kind(db, org_id, cid, "original_image")
    assert [r["seq"] for r in rows] == [0, 1, 2]


async def test_meta_connection_upsert_and_status(db, org_id):
    cid = await repo.upsert_meta_connection(db, org_id, "conn_1", "org1__meta_ads")
    row = await repo.get_meta_connection(db, org_id)
    assert row["id"] == cid and row["sync_status"] == "pending"

    await repo.set_meta_connection_status(db, cid, "error", last_error="broken")
    row = await repo.get_meta_connection_by_id(db, cid)
    assert row["sync_status"] == "error" and row["last_error"] == "broken"


async def test_list_and_count_creatives_exclude_unresolved_from_image_filter(
    db, org_id, account_id
):
    await repo.upsert_creative(
        db,
        org_id,
        account_id,
        _creative("c-img", "image"),
        "raw/img.json",
        format_source="meta_object_type",
    )
    await repo.upsert_creative(
        db,
        org_id,
        account_id,
        _creative("c-unresolved", "unresolved"),
        "raw/unresolved.json",
        format_source="unresolved",
    )

    # Sanity: both rows actually landed before we assert the filter excludes one.
    assert await repo.count_creatives(db, org_id, account_id, None, None, None) == 2

    rows = await repo.list_creatives(db, org_id, account_id, "image", 50, 0, None, None)
    assert [r["platform_creative_id"] for r in rows] == ["c-img"]
    assert await repo.count_creatives(db, org_id, account_id, "image", None, None) == 1


async def test_meta_connection_upsert_is_idempotent_per_org(db, org_id):
    first = await repo.upsert_meta_connection(db, org_id, "conn_1", "org1__meta_ads")
    second = await repo.upsert_meta_connection(db, org_id, "conn_2", "org1__meta_ads")
    assert first == second
    row = await repo.get_meta_connection(db, org_id)
    assert row["fivetran_connector_id"] == "conn_2"


async def test_upsert_metrics_persists_diagnosis_columns(db, org_id):
    acct = await repo.upsert_ad_account(db, org_id, "77", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    await repo.upsert_ad(db, org_id, acct, PlatformAd("ad77", "A", "ACTIVE", None, None, "cr"), cid)
    row = MetricRow(
        "ad77", date(2026, 9, 1), "all", Decimal("10"), 1000, 20, 20, Decimal("2"),
        Decimal("100"), 300, 90, 50, 30, {},
        reach=700, video_plays=400, video_p25=280, video_p75=60, video_p95=40,
        video_watch_seconds=Decimal("2840.5"), landing_page_views=15, content_views=9,
        add_to_carts=4, checkouts=3, purchases=2, post_reactions=12, comments=3, saves=5,
    )  # fmt: skip
    written, skipped = await repo.upsert_metrics(db, org_id, acct, [row])
    assert (written, skipped) == (1, 0)
    r = await db.fetchrow("SELECT * FROM metric_daily WHERE creative_id=$1", cid)
    assert r["reach"] == 700 and r["video_plays"] == 400
    assert (r["video_p25"], r["video_p50"], r["video_p75"], r["video_p95"], r["video_p100"]) == (
        280, 50, 60, 40, 30,
    )  # fmt: skip
    assert r["video_watch_seconds"] == Decimal("2840.5")
    steps = ("landing_page_views", "content_views", "add_to_carts", "checkouts", "purchases")
    assert tuple(r[k] for k in steps) == (15, 9, 4, 3, 2)
    assert (r["post_reactions"], r["comments"], r["saves"]) == (12, 3, 5)

    # Re-upsert with changed values must overwrite, not keep the old row.
    row2 = MetricRow(
        "ad77", date(2026, 9, 1), "all", Decimal("10"), 1000, 20, 20, Decimal("2"),
        Decimal("100"), 300, 90, 50, 30, {}, reach=900, purchases=6,
    )  # fmt: skip
    await repo.upsert_metrics(db, org_id, acct, [row2])
    r = await db.fetchrow(
        "SELECT reach, purchases, video_plays FROM metric_daily WHERE creative_id=$1", cid
    )
    assert (r["reach"], r["purchases"], r["video_plays"]) == (900, 6, 0)


async def test_metric_row_positional_construction_still_works():
    r = MetricRow(
        "a", date(2026, 9, 1), "all", Decimal(1), 1, 1, 1, Decimal(0), Decimal(0), 0, 0, 0, 0, {}
    )
    assert r.reach == 0 and r.purchases == 0 and r.video_watch_seconds == 0
