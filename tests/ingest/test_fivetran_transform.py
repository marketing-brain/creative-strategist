from cs.ingest import fivetran_transform, repo
from cs.ingest.fivetran_fixtures import create_fixture_schema, insert_campaign_ad_creative
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
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 1


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
