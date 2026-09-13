"""Fivetran-shaped fixture schema for local demo/e2e runs -- no live Fivetran connector or
Meta credentials needed. create_fixture_schema builds the tables fivetran/transform.py /
fivetran/insights.py read; callers insert their own rows into them."""

from __future__ import annotations

from datetime import date

from cs.core.db import Database
from cs.ingest.fivetran.schema import (
    AD_ASSET_FEED_SPEC_VIDEO,
    AD_HISTORY,
    AD_VIDEO_HISTORY,
    BASIC_AD,
    CAMPAIGN_HISTORY,
    CREATIVE_HISTORY,
    INSIGHTS_ACTION_TABLES,
    VIDEO_RETENTION_AD,
    VIDEO_RETENTION_CHILD_COLUMNS,
)


async def create_fixture_schema(db: Database, schema: str, *, with_retention: bool = True) -> None:
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(f"""
        CREATE TABLE "{schema}".{CAMPAIGN_HISTORY} (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_HISTORY} (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{CREATIVE_HISTORY} (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT, video_video_id TEXT,
          object_story_link_data_child_attachments TEXT, instagram_permalink_url TEXT,
          object_type TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_VIDEO_HISTORY} (
          id TEXT, source TEXT, picture TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} (
          creative_id TEXT, video_id TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(
        f'CREATE TABLE "{schema}".{BASIC_AD} (ad_id TEXT, date DATE, '
        f"spend NUMERIC, impressions INT, inline_link_clicks INT, reach INT)"
    )
    for t in INSIGHTS_ACTION_TABLES:
        await db.execute(
            f'CREATE TABLE "{schema}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)"
        )
    if with_retention:
        await create_retention_tables(db, schema)


async def create_retention_tables(db: Database, schema: str) -> None:
    """The video_retention_ad custom report and its unpivoted child tables, shaped like
    the ones Fivetran lands for org 2 in production."""
    await db.execute(
        f'CREATE TABLE "{schema}".{VIDEO_RETENTION_AD} (ad_id TEXT, date DATE, '
        f"account_id BIGINT, adset_id BIGINT, campaign_id BIGINT, ad_name TEXT)"
    )
    for suffix in VIDEO_RETENTION_CHILD_COLUMNS:
        await db.execute(
            f'CREATE TABLE "{schema}".{VIDEO_RETENTION_AD}_{suffix} '
            f"(ad_id TEXT, date DATE, index INT, value DOUBLE PRECISION, action_type TEXT)"
        )


async def insert_campaign_ad_creative(
    db: Database,
    schema: str,
    *,
    campaign_id: str,
    account_id: str,
    adset_id: str,
    ad_id: str,
    creative_id: str,
    name: str,
    headline: str,
    image_url: str,
    video_id: str | None = None,
    video_video_id: str | None = None,
    instagram_permalink_url: str | None = None,
    object_type: str | None = None,
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".campaign_history VALUES ($1, $2, $3, now())',
        campaign_id,
        account_id,
        f"Campaign {name}",
    )
    await db.execute(
        f'INSERT INTO "{schema}".ad_history VALUES ($1, $2, $3, $4, $5, $6, $7, now())',
        ad_id,
        account_id,
        adset_id,
        campaign_id,
        creative_id,
        name,
        "ACTIVE",
    )
    await db.execute(
        f'INSERT INTO "{schema}".creative_history VALUES '
        f"($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NULL, $12, $13, now())",
        creative_id,
        account_id,
        name,
        headline,
        "Primary text",
        "SHOP_NOW",
        image_url,
        None,
        "https://example.com",
        video_id,
        video_video_id,
        instagram_permalink_url,
        object_type,
    )


async def insert_video(db: Database, schema: str, *, video_id: str, source: str | None) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_video_history VALUES ($1, $2, NULL, now())',
        video_id,
        source,
    )


async def insert_asset_feed_spec_video(
    db: Database, schema: str, *, creative_id: str, video_id: str
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} VALUES ($1, $2, now())',
        creative_id,
        video_id,
    )


async def insert_daily_insight(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    day: str,
    spend: float,
    impressions: int,
    link_clicks: int,
    reach: int = 0,
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".basic_ad VALUES ($1, $2, $3, $4, $5, $6)',
        ad_id,
        date.fromisoformat(day),
        spend,
        impressions,
        link_clicks,
        reach,
    )


async def insert_retention(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    day: str,
    plays: float,
    p25: float,
    p50: float,
    p75: float,
    p95: float,
    p100: float,
    thruplay: float,
    avg_time_watched: float,
) -> None:
    d = date.fromisoformat(day)
    await db.execute(
        f'INSERT INTO "{schema}".{VIDEO_RETENTION_AD} VALUES ($1, $2, 1, 1, 1, $3)',
        ad_id,
        d,
        ad_id,
    )
    values = {
        "video_play_actions": plays,
        "video_p_25_watched_actions": p25,
        "video_p_50_watched_actions": p50,
        "video_p_75_watched_actions": p75,
        "video_p_95_watched_actions": p95,
        "video_p_100_watched_actions": p100,
        "video_thruplay_watched_actions": thruplay,
        "video_avg_time_watched_actions": avg_time_watched,
    }
    for suffix, v in values.items():
        await db.execute(
            f'INSERT INTO "{schema}".{VIDEO_RETENTION_AD}_{suffix} VALUES ($1, $2, 0, $3, $4)',
            ad_id,
            d,
            v,
            "video_view",
        )
