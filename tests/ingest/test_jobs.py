from datetime import date
from decimal import Decimal

import pytest

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
