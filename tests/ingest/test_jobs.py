import json
import re
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo, service
from cs.jobs.worker import run_once


@pytest.fixture
async def account_id(db, org_id, settings):
    aid = await repo.upsert_ad_account(
        db, org_id, "1", "Main", "USD", encrypt_str(settings, "tok"), None
    )
    await repo.set_ad_account_status(db, org_id, aid, "active")
    return aid


@pytest.fixture
def fake(monkeypatch):
    f = FakeConnector()
    f.add_creative(
        PlatformCreative(
            "c1", "image", "H", "P", None, "SHOP_NOW", "https://x", "https://img/1", "image/png", {}
        ),
        b"PNGDATA",
    )
    f.add_creative(
        PlatformCreative(
            "c2",
            "video",
            "V",
            "P2",
            None,
            "LEARN_MORE",
            "https://y",
            "https://vid/2",
            "video/mp4",
            {"video_id": "7"},
        ),
        b"MP4DATA",
    )
    f.add_ad(PlatformAd("ad1", "A", "ACTIVE", "as1", "cp1", "c1"))
    f.add_ad(PlatformAd("ad2", "B", "ACTIVE", "as1", "cp1", "c2"))
    f.add_metric(
        MetricRow(
            "ad1",
            date.today(),
            "facebook:feed",
            Decimal("3"),
            30,
            1,
            1,
            Decimal("0"),
            Decimal("0"),
            0,
            0,
            0,
            0,
            {},
        )
    )
    monkeypatch.setattr(service, "CONNECTOR_FACTORY", lambda settings, acct, http, blobs: f)
    return f


async def _drain(db, blobs, settings):
    while await run_once(db, blobs, settings, "w"):
        pass


async def test_backfill_ingests_and_downloads(db, blobs, settings, org_id, account_id, fake):
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    creatives = await repo.list_creatives(db, org_id)
    assert {c["format"] for c in creatives} == {"image", "video"}
    assert all(c["primary_asset_path"] for c in creatives)
    assert await db.fetchval("SELECT count(*) FROM metric_daily") == 1
    # tagging.tag_creative jobs are enqueued per download; their handler arrives in Phase 2
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job WHERE status <> 'done' AND job_type <> 'tagging.tag_creative'"
        )
        == 0
    )
    cursor = await db.fetchval("SELECT sync_cursor FROM ad_account WHERE id=$1", account_id)
    assert cursor["last_synced_until"] == date.today().isoformat()


async def test_download_failure_is_isolated(db, blobs, settings, org_id, account_id, fake):
    fake.fail_downloads.add("https://vid/2")
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    ok = await db.fetchval("SELECT count(*) FROM creative WHERE primary_asset_id IS NOT NULL")
    assert ok == 1
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job WHERE job_type='meta.download_creative' AND status='pending'"
        )
        == 1
    )


async def test_nightly_schedule_enqueues_incremental(db, settings, org_id, account_id):
    from cs.ingest.jobs import nightly_sync

    await nightly_sync(db, settings)
    row = await db.fetchrow("SELECT job_type, dedup_key FROM job")
    assert row["job_type"] == "meta.sync_incremental" and row["dedup_key"] == str(account_id)


async def test_creative_fetch_failure_is_isolated(db, blobs, settings, org_id, account_id, fake):
    fake.fail_creatives.add("c2")
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    creatives = await repo.list_creatives(db, org_id)
    assert {c["platform_creative_id"] for c in creatives} == {"c1"}
    assert await db.fetchval("SELECT count(*) FROM metric_daily") == 1
    status = await db.fetchval("SELECT status FROM job WHERE job_type = 'meta.backfill'")
    assert status == "done"
    cursor = await db.fetchval("SELECT sync_cursor FROM ad_account WHERE id=$1", account_id)
    assert cursor["last_synced_until"] == date.today().isoformat()


async def test_raw_sink_shares_timestamp_across_pages():
    class RecordingBlobs:
        def __init__(self):
            self.keys: list[str] = []

        async def put(self, key, data, content_type):
            self.keys.append(key)

    blobs = RecordingBlobs()
    sink = service._raw_sink(blobs, "org1", "act_1")
    await sink("act_1/ads", {"data": [1]})
    await sink("act_1/ads", {"data": [2]})
    assert len(blobs.keys) == 2
    prefix1, suffix1 = blobs.keys[0].rsplit("/", 1)
    prefix2, suffix2 = blobs.keys[1].rsplit("/", 1)
    assert prefix1 == prefix2
    ts = suffix1.split("-")[0]
    assert suffix1 == f"{ts}-1.json"
    assert suffix2 == f"{ts}-2.json"


async def test_raw_sink_scrubs_access_tokens_from_paging_urls(blobs):
    sink = service._raw_sink(blobs, "org1", "act_1")
    await sink(
        "act_1/ads",
        {
            "data": [{"id": "1"}],
            "paging": {
                "next": "https://graph.facebook.com/v21.0/act_1/ads?after=x&access_token=SECRET"
            },
        },
    )
    written = list((blobs.root / "org1/raw/meta/act_1/act_1_ads").glob("*.json"))
    assert len(written) == 1
    body = written[0].read_text()
    assert "SECRET" not in body and "access_token" not in body
    assert "after=x" in json.loads(body)["paging"]["next"]


@respx.mock
async def test_default_connector_factory_uses_bearer_and_stores_raw_page(
    db, blobs, settings, org_id, account_id
):
    account = await repo.get_ad_account(db, org_id, account_id)
    route = respx.get("https://graph.facebook.com/v21.0/act_1/ads").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    async with httpx.AsyncClient() as http:
        connector = service.default_connector_factory(settings, account, http, blobs)
        assert await connector.list_ads("1") == []

    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tok"
    assert "access_token=" not in str(request.url)

    raw_dir = blobs.root / f"{org_id}/raw/meta/1/act_1_ads"
    written = list(raw_dir.rglob("*"))
    assert len(written) == 1 and re.fullmatch(r"\d+-1\.json", written[0].name)


async def test_creative_is_fetched_once_per_sync(db, blobs, settings, org_id, account_id, fake):
    fake.ads = [
        PlatformAd("ad1", "A", "ACTIVE", "as1", "cp1", "c1"),
        PlatformAd("ad2", "B", "ACTIVE", "as1", "cp1", "c1"),
    ]
    fetched: list[str] = []
    original = fake.get_creative

    async def counting_get_creative(creative_id):
        fetched.append(creative_id)
        return await original(creative_id)

    fake.get_creative = counting_get_creative

    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)

    assert fetched == ["c1"]
    assert await db.fetchval("SELECT count(*) FROM creative") == 1
    assert await db.fetchval("SELECT count(*) FROM ad") == 2
    raw = list((blobs.root / f"{org_id}/raw/meta/1/creative").glob("*.json"))
    assert [f.name for f in raw] == ["c1.json"]


async def test_backfill_downloads_carousel_cards_and_sets_primary(db, blobs, settings, org_id, account_id, fake):
    fake.add_creative(
        PlatformCreative("c3", "carousel", "Swipe", "P3", None, "SHOP_NOW", "https://z", None, None, {},
                         child_asset_urls=["https://img/card0", "https://img/card1"]),
        child_assets=[b"CARD0DATA", b"CARD1DATA"],
    )
    fake.add_ad(PlatformAd("ad3", "C", "ACTIVE", "as1", "cp1", "c3"))
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    cid = await db.fetchval("SELECT id FROM creative WHERE platform_creative_id='c3'")
    assets = await db.fetch("SELECT kind, seq FROM asset WHERE creative_id=$1 ORDER BY seq", cid)
    assert [(a["kind"], a["seq"]) for a in assets] == [("original_image", 0), ("original_image", 1)]
    primary_path = await db.fetchval(
        "SELECT a.path FROM creative c JOIN asset a ON a.id = c.primary_asset_id WHERE c.id=$1", cid)
    assert primary_path is not None and "card_00" in primary_path


async def test_carousel_download_retry_does_not_duplicate_cards(
    db, blobs, settings, org_id, account_id, fake
):
    fake.add_creative(
        PlatformCreative(
            "c4", "carousel", "Swipe2", "P4", None, "SHOP_NOW", "https://z2", None, None, {},
            child_asset_urls=["https://img/card2_0", "https://img/card2_1"],
        ),
        child_assets=[b"CARD0DATA2", b"CARD1DATA2"],
    )
    fake.add_ad(PlatformAd("ad4", "D", "ACTIVE", "as1", "cp1", "c4"))
    fake.fail_downloads.add("https://img/card2_1")
    await queue.enqueue(db, org_id, "meta.backfill", {"ad_account_id": str(account_id)})
    await _drain(db, blobs, settings)
    cid = await db.fetchval("SELECT id FROM creative WHERE platform_creative_id='c4'")
    assert await db.fetchval("SELECT count(*) FROM asset WHERE creative_id=$1 AND seq=0", cid) == 1
    fake.fail_downloads.discard("https://img/card2_1")
    await db.execute(
        "UPDATE job SET run_after = now() WHERE job_type = 'meta.download_carousel'"
        " AND status = 'pending'"
    )
    await _drain(db, blobs, settings)
    assets = await db.fetch(
        "SELECT seq, count(*) AS n FROM asset WHERE creative_id=$1 GROUP BY seq ORDER BY seq", cid
    )
    assert [(a["seq"], a["n"]) for a in assets] == [(0, 1), (1, 1)]
