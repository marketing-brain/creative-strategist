import json

import httpx
import respx

from cs.connectors.base import PlatformCreative
from cs.connectors.fivetran import client as fivetran_client
from cs.core import queue, repo
from cs.ingest import jobs as ingest_jobs
from cs.ingest.fivetran.fixtures import create_fixture_schema, insert_campaign_ad_creative
from cs.jobs.worker import run_once

F = "https://api.fivetran.com/v1"
APIFY_URL = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"


@respx.mock
async def test_await_first_sync_reraises_until_fivetran_finishes(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "status": {"setup_state": "connected"}}}
        )
    )
    await queue.enqueue(
        db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)}, max_attempts=3
    )
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "pending"
    assert await db.fetchval("SELECT status FROM job") == "pending"  # retried, not dead yet


@respx.mock
async def test_await_first_sync_enqueues_sync_once_fivetran_succeeds(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "status": {"setup_state": "connected"},
                    "succeeded_at": "2026-09-01T00:00:00Z",
                }
            },
        )
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "ready"
    job_type = await db.fetchval("SELECT job_type FROM job WHERE status='pending'")
    assert job_type == "meta.sync_from_fivetran"


@respx.mock
async def test_await_first_sync_locks_down_schema_when_connected_and_still_paused(
    db, blobs, settings, org_id
):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "paused": True,
                    "status": {"setup_state": "connected"},
                }
            },
        )
    )
    reload_route = respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    for table in fivetran_client.REQUIRED_TABLES:
        respx.get(f"{F}/connectors/conn_1/schemas/s/tables/{table}/columns").mock(
            return_value=httpx.Response(200, json={"data": {"columns": {"id": {"enabled": True}}}})
        )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    unpause_route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    await queue.enqueue(
        db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)}, max_attempts=3
    )
    assert await run_once(db, blobs, settings, "w1") is True
    assert reload_route.called
    schema_sent = json.loads(schema_route.calls.last.request.content)
    assert schema_sent["schema_change_handling"] == "ALLOW_COLUMNS"
    assert set(schema_sent["schemas"]["s"]["tables"]) == set(fivetran_client.REQUIRED_TABLES)
    assert json.loads(unpause_route.calls.last.request.content) == {"paused": False}
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "pending"
    assert await db.fetchval("SELECT status FROM job") == "pending"  # still waiting on the sync


@respx.mock
async def test_await_first_sync_marks_broken_connector_as_error(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "status": {"setup_state": "broken"}}}
        )
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "error" and "reconnect" in row["last_error"].lower()


async def test_sync_from_fivetran_stays_ready_when_insights_table_is_missing(
    db, blobs, settings, org_id
):
    """Regression test: basic_ad may not have landed for this org yet -- confirmed live
    against a real connector, this crashes sync_insights with UndefinedTableError. That
    must not undo a successful entity sync or leave the connection stuck in 'error':
    ads/creatives are fully usable without metrics, and retrying does nothing to fix a
    table that doesn't exist until Fivetran lands it."""
    schema = "no_insights_org__meta_ads"
    await create_fixture_schema(db, schema)
    await db.execute(f'DROP TABLE "{schema}".basic_ad CASCADE')
    await insert_campaign_ad_creative(
        db,
        schema,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
        object_type="PHOTO",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", schema)
    # Pre-activate the account this fixture's campaign_history declares. Without this,
    # the activation gate (a separate, deliberate concern -- see
    # test_paused_account_is_discovered_but_not_ingested) blocks all ingestion, and this
    # test would pass even if entity sync were completely broken -- proving nothing about
    # the "ads/creatives are fully usable without metrics" claim in the docstring above.
    acct_id = await repo.upsert_ad_account(db, org_id, "999", "999", "USD", conn_id)
    await repo.set_ad_account_status(db, org_id, acct_id, "active")

    await queue.enqueue(db, org_id, "meta.sync_from_fivetran", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True

    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "ready"
    assert (
        await db.fetchval("SELECT status FROM job WHERE job_type='meta.sync_from_fivetran'")
        == "done"
    )
    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1

    # The actual claim under test: entity sync (ads/creatives) succeeds and is usable
    # even though insights sync failed on the missing table.
    creative = await db.fetchrow("SELECT * FROM creative WHERE org_id=$1", org_id)
    # The image label is stored when its download lands, not before.
    assert creative is not None and creative["format"] == "unresolved"
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 1
    download_job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.download_creative'")
    assert download_job is not None and download_job["status"] == "pending"


async def test_nightly_sync_enqueues_sync_for_ready_connections_with_active_account(
    db, settings, org_id
):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await repo.set_meta_connection_status(db, conn_id, "ready")
    acct_id = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)
    await repo.set_ad_account_status(db, org_id, acct_id, "active")

    await ingest_jobs.nightly_sync(db, settings)

    job = await db.fetchrow(
        "SELECT * FROM job WHERE job_type='meta.sync_from_fivetran' AND org_id=$1", org_id
    )
    assert job is not None
    assert job["payload"]["meta_connection_id"] == str(conn_id)


async def test_nightly_sync_skips_connections_without_an_active_account(db, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await repo.set_meta_connection_status(db, conn_id, "ready")
    await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)  # stays 'paused'

    await ingest_jobs.nightly_sync(db, settings)

    assert await db.fetchval("SELECT count(*) FROM job WHERE org_id=$1", org_id) == 0


async def _creative(db, org_id):
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        # Stored unresolved: no file yet, so the label waits for the download.
        PlatformCreative(
            "cr1", "unresolved", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}
        ),
        "",
        format_source="unresolved",
    )
    return cid


@respx.mock
async def test_resolve_instagram_video_downloads_and_tags_on_success(db, blobs, settings, org_id):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[{"videoUrl": "https://cdn.instagram/vid.mp4"}])
    )
    respx.get("https://cdn.instagram/vid.mp4").mock(
        return_value=httpx.Response(200, content=b"vid", headers={"content-type": "video/mp4"})
    )
    cid = await _creative(db, org_id)
    await queue.enqueue(
        db,
        org_id,
        "meta.resolve_instagram_video",
        {
            "creative_id": str(cid),
            "permalink_url": "https://www.instagram.com/p/abc/",
            "kind": "original_video",
            "format": "video",
            "format_source": "meta_object_type",
        },
        dedup_key=str(cid),
        max_attempts=3,
    )
    assert await run_once(db, blobs, settings, "w1") is True
    assert await repo.creative_has_primary_asset(db, org_id, cid)
    assert (
        await db.fetchval("SELECT status FROM job WHERE job_type='meta.resolve_instagram_video'")
        == "done"
    )
    assert (
        await db.fetchval("SELECT job_type FROM job WHERE job_type='tagging.tag_creative'")
        == "tagging.tag_creative"
    )
    creative = await db.fetchrow("SELECT format, format_source FROM creative WHERE id=$1", cid)
    assert (creative["format"], creative["format_source"]) == ("video", "meta_object_type")


@respx.mock
async def test_resolve_instagram_video_gives_up_without_retry_when_post_is_gone(
    db, blobs, settings, org_id
):
    """Apify returning no items (deleted/private post) is a definitive answer -- the job
    must mark done, not retry, so a nightly sync doesn't keep paying Apify to re-learn
    the same thing forever."""
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[]))
    cid = await _creative(db, org_id)
    await queue.enqueue(
        db,
        org_id,
        "meta.resolve_instagram_video",
        {
            "creative_id": str(cid),
            "permalink_url": "https://www.instagram.com/p/gone/",
            "kind": "original_video",
        },
        dedup_key=str(cid),
        max_attempts=3,
    )
    assert await run_once(db, blobs, settings, "w1") is True
    assert await db.fetchval("SELECT status FROM job") == "done"
    assert await repo.creative_has_primary_asset(db, org_id, cid) is False


@respx.mock
async def test_resolve_instagram_video_retries_on_transient_apify_failure(
    db, blobs, settings, org_id
):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(return_value=httpx.Response(503, text="rate limited"))
    cid = await _creative(db, org_id)
    await queue.enqueue(
        db,
        org_id,
        "meta.resolve_instagram_video",
        {
            "creative_id": str(cid),
            "permalink_url": "https://www.instagram.com/p/x/",
            "kind": "original_video",
        },
        dedup_key=str(cid),
        max_attempts=3,
    )
    assert await run_once(db, blobs, settings, "w1") is True
    assert await db.fetchval("SELECT status FROM job") == "pending"  # retried, not dead yet


FB_VIDEO_URL = (
    "https://api.apify.com/v2/acts/automation-lab~facebook-public-video-downloader/"
    "run-sync-get-dataset-items"
)


def _fb_item(video_id, media_url=None):
    return {
        "sourceUrl": f"https://www.facebook.com/watch/?v={video_id}",
        "sourceMediaUrl": media_url,
        "retrievalStatus": "resolved" if media_url else "failed",
        "error": None if media_url else f"ERROR: [facebook] {video_id}: Cannot parse data",
    }


async def _enqueue_facebook_resolve(db, org_id, cid, video_ids):
    await queue.enqueue(
        db,
        org_id,
        "meta.resolve_facebook_video",
        {
            "creative_id": str(cid),
            "video_ids": video_ids,
            "kind": "original_video",
            "format": "video",
            "format_source": "asset_feed_spec_video",
        },
        dedup_key=str(cid),
        max_attempts=3,
    )


@respx.mock
async def test_resolve_facebook_video_downloads_the_first_playable_id_in_order(
    db, blobs, settings, org_id
):
    settings.apify_api_key = "test-token"
    respx.post(FB_VIDEO_URL).mock(return_value=httpx.Response(200, json=[
        _fb_item("v_feed"),
        _fb_item("v_story", "https://video.fbcdn/story.mp4"),
        _fb_item("v_reels", "https://video.fbcdn/reels.mp4"),
    ]))
    respx.get("https://video.fbcdn/story.mp4").mock(
        return_value=httpx.Response(200, content=b"vid", headers={"content-type": "video/mp4"})
    )
    cid = await _creative(db, org_id)
    await _enqueue_facebook_resolve(db, org_id, cid, ["v_feed", "v_story", "v_reels"])

    assert await run_once(db, blobs, settings, "w1") is True

    assert await repo.creative_has_primary_asset(db, org_id, cid)
    status = await db.fetchval(
        "SELECT status FROM job WHERE job_type='meta.resolve_facebook_video'"
    )
    assert status == "done"
    creative = await db.fetchrow("SELECT format, format_source FROM creative WHERE id=$1", cid)
    assert (creative["format"], creative["format_source"]) == ("video", "asset_feed_spec_video")


@respx.mock
async def test_resolve_facebook_video_gives_up_when_no_id_plays(db, blobs, settings, org_id):
    # Unpublished ad-only videos are never publicly served; retrying learns nothing.
    settings.apify_api_key = "test-token"
    respx.post(FB_VIDEO_URL).mock(return_value=httpx.Response(200, json=[
        _fb_item("v1"),
    ]))
    cid = await _creative(db, org_id)
    await _enqueue_facebook_resolve(db, org_id, cid, ["v1"])

    assert await run_once(db, blobs, settings, "w1") is True

    assert await db.fetchval("SELECT status FROM job") == "done"
    assert await repo.creative_has_primary_asset(db, org_id, cid) is False


@respx.mock
async def test_resolve_facebook_video_retries_on_transient_apify_failure(
    db, blobs, settings, org_id
):
    settings.apify_api_key = "test-token"
    respx.post(FB_VIDEO_URL).mock(return_value=httpx.Response(503, text="rate limited"))
    cid = await _creative(db, org_id)
    await _enqueue_facebook_resolve(db, org_id, cid, ["v1"])

    assert await run_once(db, blobs, settings, "w1") is True

    assert await db.fetchval("SELECT status FROM job") == "pending"


async def test_nightly_sync_skips_non_ready_connections(db, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")  # stays 'pending'
    acct_id = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)
    await repo.set_ad_account_status(db, org_id, acct_id, "active")

    await ingest_jobs.nightly_sync(db, settings)

    assert await db.fetchval("SELECT count(*) FROM job WHERE org_id=$1", org_id) == 0
