from cs.ingest import fivetran_transform, repo
from cs.ingest.fivetran_fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_video,
)
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def test_discover_and_sync_entities(db, blobs, settings, org_id):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["accounts_discovered"] == 1
    assert summary["failed"] == 0
    accounts = await repo.list_ad_accounts(db, org_id)
    assert len(accounts) == 1 and accounts[0]["platform_account_id"] == "999"
    assert accounts[0]["status"] == "paused"

    # Discovery alone must not pull any content -- a still-paused account is a pick-list
    # entry only, not an implicit grant to download and expose its ads.
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 0
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 0

    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    summary2 = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    assert summary2["failed"] == 0

    ads = await db.fetch("SELECT * FROM ad WHERE org_id=$1", org_id)
    assert len(ads) == 1 and ads[0]["platform_ad_id"] == "ad1" and ads[0]["status"] == "ACTIVE"

    creatives = await db.fetch("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert len(creatives) == 1
    c = creatives[0]
    assert c["platform_creative_id"] == "cr1"
    assert c["format"] == "image"
    assert c["headline"] == "Headline" and c["primary_text"] == "Primary text"
    assert c["cta_type"] == "SHOP_NOW" and c["link_url"] == "https://example.com"


async def test_discover_and_sync_entities_is_idempotent(db, blobs, settings, org_id):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 1


async def test_paused_account_is_discovered_but_not_ingested(db, blobs, settings, org_id):
    """The activation gate itself: a discovered account with no explicit activation must
    get an ad_account row (so the user has something to activate) and nothing else -- no
    ad, no creative, no download/tagging job -- regardless of what Meta/Fivetran-level
    scope the connect flow granted."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["accounts_discovered"] == 1
    assert summary["synced_ads"] == 0
    assert summary["new_creatives"] == 0
    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 0
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 0
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative'") == 0
    )


async def test_dead_download_job_is_reenqueued_on_resync(db, blobs, settings, org_id):
    """Regression test for the `if created:` gate bug: a meta.download_creative job that
    dies after exhausting retries (CDN 404, expired Meta URL, transient network failure --
    never inserts an asset row) must still be re-enqueued on the *next* sync. upsert_creative's
    `created` flag being False on a repeat sync (because the creative row already exists)
    must not suppress the enqueue -- only an already-present asset should do that."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    first_job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.download_creative'")
    assert first_job is not None and first_job["status"] == "pending"

    # Simulate the download job dying after exhausting all its retries. No asset ever
    # gets inserted for this creative.
    await db.execute("UPDATE job SET status='dead' WHERE id=$1", first_job["id"])

    # A later sync (e.g. the nightly resync) must notice the asset is still missing and
    # re-enqueue the download -- not silently leave it dead forever.
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    pending = await db.fetch(
        "SELECT * FROM job WHERE job_type='meta.download_creative' AND status='pending'"
    )
    assert len(pending) == 1


async def test_one_malformed_ad_does_not_abort_the_whole_sync(
    db, blobs, settings, org_id, monkeypatch
):
    """Regression test for lost per-row fault isolation: one ad/creative row that blows up
    during processing must not prevent other, valid rows in the same sync from being
    processed -- and must be counted in the returned summary rather than aborting the sync."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad_bad",
        creative_id="bad",
        name="bad",
        headline="Bad",
        image_url="https://img/bad.jpg",
    )
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad_good",
        creative_id="good",
        name="good",
        headline="Good",
        image_url="https://img/good.jpg",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")

    original_upsert_creative = repo.upsert_creative

    async def flaky_upsert_creative(db_, org_id_, acct_id_, platform_creative, raw_path):
        if platform_creative.platform_creative_id == "bad":
            raise ValueError("boom: malformed row")
        return await original_upsert_creative(db_, org_id_, acct_id_, platform_creative, raw_path)

    monkeypatch.setattr(fivetran_transform.repo, "upsert_creative", flaky_upsert_creative)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 1
    assert summary["synced_ads"] == 1
    assert summary["new_creatives"] == 1

    creatives = await db.fetch("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert len(creatives) == 1 and creatives[0]["platform_creative_id"] == "good"


async def test_bigint_platform_ids_are_stringified(db, blobs, settings, org_id):
    """Regression test: confirmed against a real Fivetran connector that campaign_history/
    ad_set_history/ad_history/creative_history all land id/account_id/campaign_id/
    ad_set_id/creative_id as bigint, not text -- the fixture schema (and every other test
    in this file) uses TEXT columns with human-readable ids for readability, which never
    exercised this. Without str() coercion, asyncpg raises DataError binding an int for
    ad_account.platform_account_id's TEXT column, and the whole sync throws before any
    account is even discovered."""
    schema = "bigint_org__meta_ads"
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(
        f'CREATE TABLE "{schema}".campaign_history (id BIGINT, account_id BIGINT, '
        f"name TEXT, _fivetran_synced TIMESTAMPTZ)"
    )
    await db.execute(
        f'CREATE TABLE "{schema}".ad_history (id BIGINT, account_id BIGINT, '
        f"ad_set_id BIGINT, campaign_id BIGINT, creative_id BIGINT, name TEXT, "
        f"status TEXT, _fivetran_synced TIMESTAMPTZ)"
    )
    await db.execute(
        f'CREATE TABLE "{schema}".creative_history (id BIGINT, account_id BIGINT, '
        f"name TEXT, title TEXT, body TEXT, call_to_action_type TEXT, "
        f"image_url TEXT, thumbnail_url TEXT, object_story_link_data_link TEXT, "
        f"video_id BIGINT, object_story_link_data_child_attachments JSONB, "
        f"_fivetran_synced TIMESTAMPTZ)"
    )
    await db.execute(
        f'CREATE TABLE "{schema}".ad_video_history (id TEXT, source TEXT, '
        f"_fivetran_synced TIMESTAMPTZ)"
    )
    await db.execute(
        f'INSERT INTO "{schema}".campaign_history '
        "VALUES (5001, 266634581427459, 'Campaign', now())"
    )
    await db.execute(
        f'INSERT INTO "{schema}".ad_history VALUES '
        f"(9001, 266634581427459, 7001, 5001, 3001, 'Ad', 'ACTIVE', now())"
    )
    await db.execute(
        f'INSERT INTO "{schema}".creative_history VALUES '
        f"(3001, 266634581427459, 'name', 'Headline', 'Body', 'SHOP_NOW', "
        f"'https://img/orig.jpg', NULL, 'https://dest.example', NULL, NULL, now())"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", schema)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    assert summary["accounts_discovered"] == 1
    assert summary["failed"] == 0

    account = (await repo.list_ad_accounts(db, org_id))[0]
    assert account["platform_account_id"] == "266634581427459"
    await repo.set_ad_account_status(db, org_id, account["id"], "active")

    summary2 = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    assert summary2["failed"] == 0
    assert summary2["synced_ads"] == 1

    ad = await db.fetchrow("SELECT * FROM ad WHERE org_id=$1", org_id)
    assert ad["platform_ad_id"] == "9001"
    creative = await db.fetchrow("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert creative["platform_creative_id"] == "3001"


async def test_video_creative_downloads_from_ad_video_history_source(db, blobs, settings, org_id):
    """Regression test: video creatives used to get a creative row with format='video'
    and nothing else -- no asset ever downloaded, since creative_history alone has no
    playable URL. ad_video_history.source (confirmed against a real connector) is the
    actual downloadable MP4; this must get enqueued as a normal meta.download_creative
    job the same way an image asset_url would."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
        video_id="vid1",
    )
    await insert_video(db, SCHEMA, video_id="vid1", source="https://cdn.example/video.mp4")
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    creative = await db.fetchrow("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert creative["format"] == "video"
    job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.download_creative'")
    assert job is not None
    assert job["payload"]["asset_url"] == "https://cdn.example/video.mp4"
    assert job["payload"]["kind"] == "original_video"


async def test_video_with_no_landed_source_yet_is_skipped_not_failed(db, blobs, settings, org_id):
    """A video whose ad_video_history row hasn't synced yet (or has no source -- still
    processing on Meta's side) must not fail the sync or enqueue a broken download --
    same graceful skip an image creative with no image_url gets."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url="https://img/orig.jpg",
        video_id="vid_missing",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    creative = await db.fetchrow("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert creative["format"] == "video"
    n = await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative'")
    assert n == 0


def test_carousel_urls_returns_none_for_malformed_shapes():
    """Regression test: a valid-but-non-list JSON value (e.g. a bare number) must return
    None gracefully instead of raising a TypeError out of the list comprehension."""
    assert fivetran_transform._carousel_urls(None) is None
    assert fivetran_transform._carousel_urls("") is None
    assert fivetran_transform._carousel_urls("not json") is None
    assert fivetran_transform._carousel_urls("42") is None
    assert fivetran_transform._carousel_urls('"a string"') is None
    assert fivetran_transform._carousel_urls("[]") is None
    assert fivetran_transform._carousel_urls('[{"picture": "https://x/1.jpg"}]') == [
        "https://x/1.jpg"
    ]
