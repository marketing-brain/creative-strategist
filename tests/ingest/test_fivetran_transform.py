from datetime import date

import asyncpg
import pytest

from cs.core import repo
from cs.ingest.fivetran import transform as fivetran_transform
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema,
    insert_asset_feed_spec_video,
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
        object_type="PHOTO",
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
        object_type="PHOTO",
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

    async def flaky_upsert_creative(
        db_, org_id_, acct_id_, platform_creative, raw_path, *, format_source
    ):
        if platform_creative.platform_creative_id == "bad":
            raise ValueError("boom: malformed row")
        return await original_upsert_creative(
            db_, org_id_, acct_id_, platform_creative, raw_path, format_source=format_source
        )

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
        f"video_video_id BIGINT, object_type TEXT, "
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
        f"'https://img/orig.jpg', NULL, 'https://dest.example', NULL, NULL, "
        f"NULL, 'PHOTO', now())"
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


async def test_video_view_loader_joins_bigint_ad_id_against_text_ad_history_id(
    db, blobs, settings, org_id
):
    """Regression test: Meta's ad_id lands as bigint on basic_ad_actions on some real
    Fivetran connectors (confirmed by this repo's own
    test_bigint_ad_id_still_joins_against_our_text_ad_table for the insights path) while
    ad_history.id is text in our fixture convention -- the video-view membership loader's
    join must not assume either side's type. Without casting both sides to text,
    `act.ad_id = a.id::text` is `bigint = text`, which Postgres has no operator for, and
    asyncpg raises UndefinedFunctionError for the whole org's sync."""
    await create_fixture_schema(db, SCHEMA)
    # ad_history.id is TEXT in this fixture schema (fine -- a real connector lands it as
    # bigint too, and the join must tolerate either), but it must hold a numeric string
    # so it can actually match a BIGINT ad_id on the other side of the join once both are
    # cast to text.
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="9001",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url=None,
        object_type="SHARE",
    )
    # Fixture schema's basic_ad_actions is TEXT-typed -- replace it with a BIGINT-typed
    # one to exercise the real-connector shape the join must also tolerate.
    await db.execute(f'DROP TABLE "{SCHEMA}".basic_ad_actions')
    await db.execute(
        f'CREATE TABLE "{SCHEMA}".basic_ad_actions '
        f"(ad_id BIGINT, date DATE, action_type TEXT, index INT, value NUMERIC)"
    )
    await db.execute(
        f"INSERT INTO \"{SCHEMA}\".basic_ad_actions VALUES (9001, '2026-09-01', 'video_view', 0, 1)"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert (row["format"], row["format_source"]) == ("video", "video_view_metric")


async def _ad_with_impressions(db, *, ad_id: str, impressions: int) -> None:
    """A second ad row for creative cr1, plus its basic_ad impressions."""
    await db.execute(
        f'INSERT INTO "{SCHEMA}".ad_history VALUES ($1, $2, $3, $4, $5, $6, $7, now())',
        ad_id, "999", "as1", "c1", "cr1", "name", "ACTIVE",
    )
    await db.execute(
        f"INSERT INTO \"{SCHEMA}\".basic_ad VALUES ($1, '2026-09-01', 1.0, $2, 0)",
        int(ad_id), impressions,
    )


async def _schema_with_bigint_basic_ad(db) -> None:
    await create_fixture_schema(db, SCHEMA)
    await db.execute(f'DROP TABLE "{SCHEMA}".basic_ad')
    await db.execute(
        f'CREATE TABLE "{SCHEMA}".basic_ad (ad_id BIGINT, date DATE, '
        f"spend NUMERIC, impressions INT, inline_link_clicks INT)"
    )


async def test_impressions_loader_sums_each_creative_across_its_ads(db):
    # Two sub-threshold ads (30, 25) sum to 55; BIGINT basic_ad.ad_id is the real shape.
    await _schema_with_bigint_basic_ad(db)
    await _ad_with_impressions(db, ad_id="9001", impressions=30)
    await _ad_with_impressions(db, ad_id="9002", impressions=25)
    await db.execute(
        f'INSERT INTO "{SCHEMA}".basic_ad_actions VALUES ($1, $2, $3, 0, 1)',
        "9001", date(2026, 9, 1), "link_click",
    )

    assert await fivetran_transform._impressions_by_creative(db, SCHEMA) == {"cr1": 55}


async def test_impressions_loader_returns_none_when_actions_table_is_missing(db):
    # No actions table means "no data", not "no views" -- else every video becomes an image.
    await _schema_with_bigint_basic_ad(db)
    await _ad_with_impressions(db, ad_id="9001", impressions=100_000)
    await db.execute(f'DROP TABLE "{SCHEMA}".basic_ad_actions')

    assert await fivetran_transform._impressions_by_creative(db, SCHEMA) is None


async def test_sync_stores_zero_views_boosted_post_as_image(db, blobs, settings, org_id):
    # End to end: a boosted post that served 5,000 times with no video views is an image.
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db, SCHEMA, campaign_id="c1", account_id="999", adset_id="as1", ad_id="9001",
        creative_id="cr1", name="n", headline="H", image_url="https://cdn/i.jpg",
        object_type="SHARE",
    )
    await db.execute(
        f'INSERT INTO "{SCHEMA}".basic_ad VALUES ($1, $2, 1.0, 5000, 0)', "9001", date(2026, 9, 1)
    )
    await db.execute(
        f'INSERT INTO "{SCHEMA}".basic_ad_actions VALUES ($1, $2, $3, 0, 7)',
        "9001", date(2026, 9, 1), "link_click",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert (row["format"], row["format_source"]) == ("image", "no_video_views")


async def test_impressions_loader_returns_none_when_actions_table_is_empty(db):
    # Synced-but-empty actions data proves nothing about views either.
    await _schema_with_bigint_basic_ad(db)
    await _ad_with_impressions(db, ad_id="9001", impressions=100_000)

    assert await fivetran_transform._impressions_by_creative(db, SCHEMA) is None


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


async def test_video_resolves_via_video_video_id_when_video_id_does_not_match(
    db, blobs, settings, org_id
):
    """Regression: video_id and video_video_id reference different Meta objects --
    video_id rarely matches ad_video_history at all (confirmed against a real connector,
    the actual join key is video_video_id). A creative with a video_id that doesn't
    resolve must still download its video if video_video_id does."""
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
        video_id="asset_feed_spec_vid",
        video_video_id="vid1",
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
    job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.download_creative'")
    assert job is not None
    assert job["payload"]["asset_url"] == "https://cdn.example/video.mp4"


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
        object_type="VIDEO",
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


async def test_unresolved_video_with_instagram_permalink_enqueues_resolve_job(
    db, blobs, settings, org_id
):
    """A video whose video_video_id doesn't resolve against ad_video_history at all, but
    that carries an instagram_permalink_url, is a Partnership Ad -- the video lives in
    the creator's own Instagram account, not this ad account's video library. That case
    should fall through to meta.resolve_instagram_video instead of being silently
    skipped like a genuinely-not-synced-yet video."""
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
        video_id="vid_not_in_video_history",
        instagram_permalink_url="https://www.instagram.com/p/abc123/",
        object_type="VIDEO",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.resolve_instagram_video'")
    assert job is not None
    assert job["payload"]["permalink_url"] == "https://www.instagram.com/p/abc123/"
    assert job["max_attempts"] == 3
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative'") == 0
    )


async def test_unresolved_video_without_instagram_permalink_is_skipped_not_enqueued(
    db, blobs, settings, org_id
):
    """No video source AND no Instagram permalink -- there's nowhere left to look, so
    this must stay a silent skip (same as before this feature existed), not an attempt
    to call Apify with no URL."""
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
        video_id="vid_not_in_video_history",
        object_type="VIDEO",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    assert await db.fetchval("SELECT count(*) FROM job") == 0


async def test_dead_resolve_job_is_not_re_enqueued_on_next_sync(db, blobs, settings, org_id):
    """Once meta.resolve_instagram_video has exhausted its retries for a creative (post
    permanently gone/private), the next nightly sync must not re-enqueue it -- otherwise
    a permanently-unresolvable video costs Apify credits forever."""
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
        video_id="vid_not_in_video_history",
        instagram_permalink_url="https://www.instagram.com/p/abc123/",
        object_type="VIDEO",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    acct = (await repo.list_ad_accounts(db, org_id))[0]
    await repo.set_ad_account_status(db, org_id, acct["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await db.execute("UPDATE job SET status='dead' WHERE job_type='meta.resolve_instagram_video'")

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    n = await db.fetchval(
        "SELECT count(*) FROM job WHERE job_type='meta.resolve_instagram_video'"
        " AND status != 'dead'"
    )
    assert n == 0


async def test_share_creative_without_evidence_is_unresolved_and_downloads_nothing(
    db, blobs, settings, org_id
):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="n",
        headline="H",
        image_url=None,
        object_type="SHARE",
    )
    await db.execute(
        f'UPDATE "{SCHEMA}".creative_history SET thumbnail_url=$1', "https://cdn/frame.jpg"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert row["format"] == "unresolved"
    assert row["format_source"] == "unresolved"
    # No asset download job may be enqueued for a creative we cannot type.
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative'") == 0
    )


async def test_video_view_loader_does_not_swallow_timeout_class_errors(db, org_id, monkeypatch):
    """The optional-signal catch in _optional_creative_ids is deliberately narrowed to
    UndefinedTableError | UndefinedColumnError -- a missing table degrades completeness,
    never soundness. asyncpg.PostgresError, the old catch, also covers
    ConnectionDoesNotExistError, QueryCanceledError (statement timeout), SerializationError
    and AdminShutdownError -- and _video_view_creative_ids is an unbounded join over the
    whole of basic_ad_actions, so a timeout there is plausible, not hypothetical. Swallowing
    one used to turn a single flaky query into a silent, org-wide 'video' -> 'unresolved'
    downgrade for every creative whose only evidence was this signal. A timeout-class error
    must propagate instead of degrading."""

    async def raise_timeout(sql, *args):
        raise asyncpg.QueryCanceledError("statement timeout")

    monkeypatch.setattr(db, "fetch", raise_timeout)

    with pytest.raises(asyncpg.QueryCanceledError):
        await fivetran_transform._video_view_creative_ids(db, SCHEMA)


async def test_video_view_loader_still_degrades_gracefully_when_table_is_missing(
    db, blobs, settings, org_id
):
    """Confirms the narrowed catch still covers its actual purpose: a genuinely-missing
    optional table (basic_ad_actions not yet landed by Fivetran for this connection) must
    still degrade to an empty set -- and hence 'unresolved', never 'video' -- rather than
    raising and aborting the whole sync, the same as before this catch was narrowed."""
    await create_fixture_schema(db, SCHEMA)
    await db.execute(f'DROP TABLE "{SCHEMA}".basic_ad_actions')
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="n",
        headline="H",
        image_url=None,
        object_type="SHARE",
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["failed"] == 0
    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert row["format"] == "unresolved"


async def test_asset_feed_spec_video_membership_classifies_as_video(db, blobs, settings, org_id):
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="n",
        headline="H",
        image_url=None,
        object_type="SHARE",
    )
    await insert_asset_feed_spec_video(db, SCHEMA, creative_id="cr1", video_id="v1")
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    row = await db.fetchrow("SELECT format, format_source FROM creative WHERE org_id=$1", org_id)
    assert (row["format"], row["format_source"]) == ("video", "asset_feed_spec_video")
