from uuid import UUID

import pytest

from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.generation import repo
from cs.generation.brand import create_brand_kit
from cs.generation.fakes import solid_png
from cs.ingest import repo as ingest_repo
from cs.ingest import service as ingest_service
from cs.jobs.worker import run_once
from cs.publish.meta import build_creative_spec


@pytest.fixture
async def approved_asset(db, org_id, blobs):
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    key = f"{org_id}/generated/{gid}/v1-1x1.jpg"
    await blobs.put(key, solid_png(64, 64), "image/jpeg")
    aid = await repo.insert_generated_asset(db, org_id, gid, "image", "1:1", 1, key, "s", "H", "P", "SHOP_NOW")
    assert await repo.set_review_state(db, org_id, aid, "approved", "u")
    return aid


def test_build_creative_spec_image():
    spec = build_creative_spec({"kind": "image", "headline": "H", "primary_text": "P", "cta_type": "SHOP_NOW"},
                               {"link_url": "https://x", "page_id": "p1"}, image_hash="h", video_id=None)
    assert spec["object_story_spec"]["page_id"] == "p1"
    ld = spec["object_story_spec"]["link_data"]
    assert ld["image_hash"] == "h" and ld["name"] == "H" and ld["call_to_action"]["type"] == "SHOP_NOW"


async def test_publish_job_and_loop_closure(db, blobs, settings, org_id, approved_asset, monkeypatch):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    fake = FakeConnector()
    monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)
    pj = await db.fetchval(
        "INSERT INTO publish_job (id, org_id, generated_asset_id, ad_account_id, campaign_id, adset_id, ad_status_requested, form, status) "
        "VALUES ($1,$2,$3,$4,'c1','as1','PAUSED',$5,'pending') RETURNING id",
        UUID(int=7), org_id, approved_asset, acct, {"link_url": "https://x", "page_id": "p1"})
    await queue.enqueue(db, org_id, "publish.meta", {"publish_job_id": str(pj)})
    await run_once(db, blobs, settings, "w")
    row = await db.fetchrow("SELECT status, platform_creative_id, platform_ad_id FROM publish_job WHERE id=$1", pj)
    assert row["status"] == "done" and row["platform_creative_id"] == "cr_new_1" and row["platform_ad_id"] == "ad_new_1"
    assert fake.created_ads[0]["status"] == "PAUSED" and fake.uploads[0][0] == "image"

    # next sync sees the new ad and links the creative back to the generated asset
    from cs.connectors.base import PlatformAd, PlatformCreative
    fake.add_creative(PlatformCreative("cr_new_1", "image", "H", "P", None, "SHOP_NOW", "https://x", "https://img/n", "image/jpeg", {}), b"x")
    fake.add_ad(PlatformAd("ad_new_1", "My ad", "PAUSED", "as1", "c1", "cr_new_1"))
    await ingest_repo.set_ad_account_status(db, org_id, acct, "active")
    await queue.enqueue(db, org_id, "meta.sync_incremental", {"ad_account_id": str(acct)})
    while await run_once(db, blobs, settings, "w"):
        pass
    linked = await db.fetchval("SELECT generated_asset_id FROM creative WHERE platform_creative_id='cr_new_1'")
    assert linked == approved_asset
