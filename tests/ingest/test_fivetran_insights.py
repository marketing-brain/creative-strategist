from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import repo
from cs.ingest.fivetran import insights as fivetran_insights
from cs.ingest.fivetran.fixtures import create_fixture_schema, insert_daily_insight
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def _seed(db, org_id):
    await create_fixture_schema(db, SCHEMA)
    await insert_daily_insight(
        db,
        SCHEMA,
        ad_id="ad1",
        day="2026-09-01",
        spend=10.0,
        impressions=1000,
        link_clicks=20,
    )
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_actions
        VALUES ('ad1', '2026-09-01', 'omni_purchase', 0, 2)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_action_values VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 100)
    """)

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
        format_source="manual_review",
    )
    await repo.upsert_ad(db, org_id, acct, PlatformAd("ad1", "A", "ACTIVE", None, None, "cr1"), cid)
    return acct


async def test_sync_insights_folds_action_breakdowns_into_metric_daily(db, blobs, settings, org_id):
    acct = await _seed(db, org_id)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary["written"] == 1 and summary["skipped"] == 0

    rows = await db.fetch("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_account_id"] == acct
    assert row["placement"] == "all"
    assert row["spend"] == 10 and row["impressions"] == 1000 and row["clicks"] == 20
    assert row["conversions"] == 2 and row["conversion_value"] == 100
    assert row["video_thruplay"] == 0 and row["video_3s_views"] == 0


async def test_bigint_ad_id_still_joins_against_our_text_ad_table(db, blobs, settings, org_id):
    """Regression test: Meta's ad_id lands as bigint everywhere else Fivetran syncs it
    (confirmed against a real connector for the entity tables) -- basic_ad follows the
    same convention. Without str() coercion this doesn't crash: it silently fails to
    match ad.platform_ad_id (text) in a dict lookup, so every metric row gets counted as
    skipped with no error at all -- a much easier bug to miss than the entity-sync crash."""
    schema = "bigint_insights_org__meta_ads"
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(
        f'CREATE TABLE "{schema}".basic_ad (ad_id BIGINT, date DATE, '
        f"spend NUMERIC, impressions INT, inline_link_clicks INT)"
    )
    for t in ("basic_ad_actions", "basic_ad_action_values"):
        await db.execute(
            f'CREATE TABLE "{schema}".{t} (ad_id BIGINT, date DATE, action_type TEXT, '
            f"index INT, value NUMERIC)"
        )
    await db.execute(
        f"INSERT INTO \"{schema}\".basic_ad VALUES (9001, '2026-09-01', 10.0, 1000, 20)"
    )

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
        format_source="manual_review",
    )
    ad = PlatformAd("9001", "A", "ACTIVE", None, None, "cr1")
    await repo.upsert_ad(db, org_id, acct, ad, cid)

    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", schema)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary["written"] == 1 and summary["skipped"] == 0

    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["ad_account_id"] == acct and row["spend"] == 10
