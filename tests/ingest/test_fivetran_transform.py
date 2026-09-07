from cs.ingest import fivetran_transform, repo
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def _seed_fivetran_schema(db):
    await db.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{SCHEMA}"')
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".campaign_history (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".ad_history (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".creative_history (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT,
          object_story_link_data_child_attachments TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".campaign_history VALUES ('c1', '999', 'Campaign 1', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_set_history VALUES ('as1', '999', 'c1', 'Ad set 1', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_history
        VALUES ('ad1', '999', 'as1', 'c1', 'cr1', 'Ad 1', 'ACTIVE', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".creative_history
        VALUES ('cr1', '999', 'name', 'Headline', 'Primary text', 'SHOP_NOW',
                'https://img/orig.jpg', 'https://img/thumb.jpg', 'https://dest.example',
                NULL, NULL, now())
    """)


async def test_discover_and_sync_entities(db, blobs, settings, org_id):
    await _seed_fivetran_schema(db)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["accounts_discovered"] == 1
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
    assert c["cta_type"] == "SHOP_NOW" and c["link_url"] == "https://dest.example"


async def test_discover_and_sync_entities_is_idempotent(db, blobs, settings, org_id):
    await _seed_fivetran_schema(db)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 1
