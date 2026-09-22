import pytest

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import repo
from cs.ingest.fivetran import insights as fivetran_insights
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema,
    insert_daily_insight,
    insert_demographics,
    insert_retention,
)
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def _seed(db, org_id, *, with_retention=True):
    await create_fixture_schema(db, SCHEMA, with_retention=with_retention)
    await insert_daily_insight(
        db,
        SCHEMA,
        ad_id="ad1",
        day="2026-09-01",
        spend=10.0,
        impressions=1000,
        link_clicks=20,
        reach=800,
    )
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_actions VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 2, 1, 1),
        ('ad1', '2026-09-01', 'purchase', 0, 2, 1, 1)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_actions VALUES
        ('ad1', '2026-09-01', 'video_view', 0, 30),
        ('ad1', '2026-09-01', 'omni_landing_page_view', 0, 15),
        ('ad1', '2026-09-01', 'landing_page_view', 0, 15),
        ('ad1', '2026-09-01', 'view_content', 0, 9),
        ('ad1', '2026-09-01', 'omni_add_to_cart', 0, 4),
        ('ad1', '2026-09-01', 'add_to_cart', 0, 3),
        ('ad1', '2026-09-01', 'initiate_checkout', 0, 3),
        ('ad1', '2026-09-01', 'post_reaction', 0, 12),
        ('ad1', '2026-09-01', 'comment', 0, 3),
        ('ad1', '2026-09-01', 'onsite_conversion.post_save', 0, 5)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".basic_ad_action_values VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 100, 80, 20)
    """)
    if with_retention:
        await insert_retention(
            db,
            SCHEMA,
            ad_id="ad1",
            day="2026-09-01",
            plays=400,
            p25=280,
            p50=120,
            p75=60,
            p95=40,
            p100=30,
            thruplay=90,
            avg_time_watched=7.1,
        )

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr1", "video", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
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
    assert summary == {
        "written": 1,
        "skipped": 0,
        "retention_tables": True,
        "attribution_windows": True,
        "audience_rows": 0,
    }

    rows = await db.fetch("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_account_id"] == acct
    assert row["placement"] == "all"
    assert row["spend"] == 10 and row["impressions"] == 1000 and row["clicks"] == 20
    assert row["reach"] == 800
    assert row["conversions"] == 2 and row["conversion_value"] == 100
    # video_view is Meta's 3-second-view action_type -> hook-rate numerator.
    assert row["video_3s_views"] == 30
    # Step conversions: omni_* wins when present, plain action_type otherwise.
    assert row["landing_page_views"] == 15 and row["content_views"] == 9
    assert row["add_to_carts"] == 4 and row["checkouts"] == 3 and row["purchases"] == 2
    assert row["post_reactions"] == 12 and row["comments"] == 3 and row["saves"] == 5
    # Retention from the video_retention_ad custom report child tables.
    assert row["video_plays"] == 400
    curve_cols = ("video_p25", "video_p50", "video_p75", "video_p95", "video_p100")
    assert tuple(row[k] for k in curve_cols) == (280, 120, 60, 40, 30)
    assert row["video_thruplay"] == 90
    # avg_time_watched is a per-day average; stored as avg * plays so it sums across days.
    assert float(row["video_watch_seconds"]) == pytest.approx(7.1 * 400)
    # Purchases by attribution window, from the same action rows (click + view == value).
    assert row["purchases_click_7d"] == 1 and row["purchases_view_1d"] == 1
    assert row["purchase_value_click_7d"] == 80 and row["purchase_value_view_1d"] == 20


async def test_attribution_split_is_zero_when_a_window_column_is_null(db, blobs, settings, org_id):
    """Meta leaves _1_d_view NULL when an ad had no view-through purchases that day
    (org 2 has such rows): NULL is zero, not a missing split."""
    await _seed(db, org_id, with_retention=False)
    await db.execute(
        f'UPDATE "{SCHEMA}".basic_ad_actions SET _1_d_view = NULL, _7_d_click = 2 '
        "WHERE action_type IN ('omni_purchase', 'purchase')"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_insights.sync_insights(ctx, org_id, connection)
    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["purchases_click_7d"] == 2 and row["purchases_view_1d"] == 0


async def test_an_empty_action_value_falls_back_to_its_windows(db, blobs, settings, org_id):
    """Meta sends some action rows with `value` NULL and only the windows filled (seen on
    org 3 from 2026-09-14). One such row used to fail the whole metrics sync."""
    await _seed(db, org_id, with_retention=False)
    await db.execute(
        f'UPDATE "{SCHEMA}".basic_ad_actions SET value = NULL, _7_d_click = NULL, _1_d_view = 1 '
        "WHERE action_type = 'omni_add_to_cart'"
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary["written"] == 1
    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["add_to_carts"] == 1 and row["purchases"] == 2


async def test_sync_insights_without_retention_report_writes_zeros(db, blobs, settings, org_id):
    """Org 1's Fivetran schema has no video_retention_ad tables at all. The sync must
    still land everything else and say so in the summary, never raise."""
    await _seed(db, org_id, with_retention=False)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary == {
        "written": 1,
        "skipped": 0,
        "retention_tables": False,
        "attribution_windows": True,
        "audience_rows": 0,
    }
    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["video_3s_views"] == 30 and row["purchases"] == 2
    assert row["video_plays"] == 0 and row["video_p25"] == 0 and row["video_thruplay"] == 0
    assert row["video_watch_seconds"] == 0


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
    # These action tables carry no attribution-window columns: the split stays zero.
    assert summary["attribution_windows"] is False

    row = await db.fetchrow("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert row["ad_account_id"] == acct and row["spend"] == 10
    assert row["purchases_click_7d"] == 0 and row["purchase_value_view_1d"] == 0


async def test_sync_folds_the_audience_breakdown(db, blobs, settings, org_id):
    """The monthly age x gender report lands in its own table, and metric_daily does not pick
    its impressions up a second time."""
    acct = await _seed(db, org_id)
    await insert_demographics(
        db, SCHEMA, ad_id="ad1", month="2026-09", age="25-34", gender="female", impressions=9_000
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary["audience_rows"] == 1 and "placement_rows" not in summary

    aud = await db.fetchrow("SELECT * FROM metric_audience WHERE org_id=$1", org_id)
    assert aud["ad_account_id"] == acct and aud["creative_id"] is not None
    # Meta reports the month as 'YYYY-MM'; it is stored as that month's first day.
    assert aud["month"].isoformat() == "2026-09-01"
    assert aud["age"] == "25-34" and aud["gender"] == "female" and aud["impressions"] == 9_000

    # metric_daily must not pick up the breakdown impressions a second time.
    daily = await db.fetchrow("SELECT impressions FROM metric_daily WHERE org_id=$1", org_id)
    assert daily["impressions"] == 1000
