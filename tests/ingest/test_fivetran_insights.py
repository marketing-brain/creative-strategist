from cs.connectors.base import PlatformAd, PlatformCreative
from cs.ingest import fivetran_insights, repo
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def _seed(db, org_id):
    await db.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{SCHEMA}"')
    await db.execute(
        f'CREATE TABLE "{SCHEMA}".ad_insights (ad_id TEXT, date DATE, '
        f"publisher_platform TEXT, platform_position TEXT, spend NUMERIC, "
        f"impressions INT, clicks INT, inline_link_clicks INT)"
    )
    for t in (
        "ad_insights_actions",
        "ad_insights_action_values",
        "ad_insights_video_play_actions",
        "ad_insights_video_thruplay_watched_actions",
        "ad_insights_video_p50_watched_actions",
        "ad_insights_video_p100_watched_actions",
    ):
        await db.execute(
            f'CREATE TABLE "{SCHEMA}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)"
        )

    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights VALUES
        ('ad1', '2026-09-01', 'facebook', 'feed', 10.0, 1000, 20, 20)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_actions
        VALUES ('ad1', '2026-09-01', 'omni_purchase', 0, 2)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_action_values VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 100)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_video_thruplay_watched_actions VALUES
        ('ad1', '2026-09-01', 'video_view', 0, 5)
    """)

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
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
    assert row["placement"] == "facebook:feed"
    assert row["spend"] == 10 and row["impressions"] == 1000 and row["clicks"] == 20
    assert row["conversions"] == 2 and row["conversion_value"] == 100
    assert row["video_thruplay"] == 5 and row["video_3s_views"] == 0
