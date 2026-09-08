"""Fivetran-shaped fixture schema for local demo/e2e runs -- no live Fivetran connector or
Meta credentials needed. create_fixture_schema builds the tables fivetran_transform.py /
fivetran_insights.py read; callers insert their own rows into them."""

from __future__ import annotations

from datetime import date

from cs.core.db import Database


async def create_fixture_schema(db: Database, schema: str) -> None:
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(f"""
        CREATE TABLE "{schema}".campaign_history (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_history (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".creative_history (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT,
          object_story_link_data_child_attachments TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_video_history (
          id TEXT, source TEXT, picture TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(
        f'CREATE TABLE "{schema}".ad_insights (ad_id TEXT, date DATE, '
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
            f'CREATE TABLE "{schema}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)"
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
        f"($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NULL, now())",
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
    )


async def insert_video(db: Database, schema: str, *, video_id: str, source: str | None) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_video_history VALUES ($1, $2, NULL, now())',
        video_id,
        source,
    )


async def insert_daily_insight(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    day: str,
    spend: float,
    impressions: int,
    clicks: int,
    link_clicks: int,
    placement: tuple[str, str] = ("facebook", "feed"),
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_insights VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
        ad_id,
        date.fromisoformat(day),
        placement[0],
        placement[1],
        spend,
        impressions,
        clicks,
        link_clicks,
    )
