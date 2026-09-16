"""Fivetran-shaped fixture schema for local demo/e2e runs -- no live Fivetran connector or
Meta credentials needed. create_fixture_schema builds the tables fivetran/transform.py /
fivetran/insights.py read; callers insert their own rows into them."""

from __future__ import annotations

import json
from datetime import date

from cs.core.db import Database
from cs.ingest.fivetran.schema import (
    AD_ASSET_CUSTOMIZATION_RULE,
    AD_ASSET_FEED_SPEC_IMAGE,
    AD_ASSET_FEED_SPEC_VIDEO,
    AD_HISTORY,
    AD_IMAGE_HISTORY,
    AD_VIDEO_HISTORY,
    BASIC_AD,
    CAMPAIGN_HISTORY,
    CREATIVE_HISTORY,
    DEMOGRAPHICS_AD,
    INSIGHTS_ACTION_TABLES,
    PLACEMENT_AD,
    PLACEMENT_CHILD_COLUMNS,
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
          id TEXT, source TEXT, picture TEXT, length REAL, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} (
          creative_id TEXT, video_id TEXT, index INT, adlabels TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_ASSET_FEED_SPEC_IMAGE} (
          creative_id TEXT, index INT, hash TEXT, adlabels TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_ASSET_CUSTOMIZATION_RULE} (
          creative_id TEXT, index INT, image_label TEXT, video_label TEXT,
          customization_spec TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_IMAGE_HISTORY} (
          hash TEXT, id TEXT, url TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(
        f'CREATE TABLE "{schema}".{BASIC_AD} (ad_id TEXT, date DATE, '
        f"spend NUMERIC, impressions INT, inline_link_clicks INT, reach INT)"
    )
    # _7_d_click / _1_d_view are the attribution-window columns Fivetran lands for org 2;
    # they come last so positional 5-value inserts keep working (window columns NULL).
    for t in INSIGHTS_ACTION_TABLES:
        await db.execute(
            f'CREATE TABLE "{schema}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC, "
            f"_7_d_click DOUBLE PRECISION, _1_d_view DOUBLE PRECISION)"
        )
    if with_retention:
        await create_retention_tables(db, schema)
    await create_breakdown_tables(db, schema)


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


async def create_breakdown_tables(db: Database, schema: str) -> None:
    """The weekly placement report (with its unpivoted video child tables and actions) and
    the monthly age x gender report, shaped like the tables Fivetran lands in production.
    `month` really is text there, formatted 'YYYY-MM'."""
    await db.execute(
        f'CREATE TABLE "{schema}".{PLACEMENT_AD} (ad_id TEXT, week DATE, _fivetran_id TEXT, '
        "publisher_platform TEXT, platform_position TEXT, account_id BIGINT, "
        "impressions INT, reach INT, spend NUMERIC, inline_link_clicks INT)"
    )
    for suffix in (*PLACEMENT_CHILD_COLUMNS, "actions"):
        await db.execute(
            f'CREATE TABLE "{schema}".{PLACEMENT_AD}_{suffix} '
            "(ad_id TEXT, week DATE, _fivetran_id TEXT, index INT, value DOUBLE PRECISION, "
            "action_type TEXT)"
        )
    await db.execute(
        f'CREATE TABLE "{schema}".{DEMOGRAPHICS_AD} (ad_id TEXT, month TEXT, _fivetran_id TEXT, '
        "age TEXT, gender TEXT, account_id BIGINT, "
        "impressions INT, reach INT, spend NUMERIC, inline_link_clicks INT)"
    )


async def insert_placement(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    week: str,
    publisher_platform: str,
    platform_position: str,
    impressions: int,
    link_clicks: int,
    reach: int = 0,
    spend: float = 0.0,
    video_3s_views: int = 0,
    plays: int = 0,
    thruplay: int = 0,
) -> None:
    fivetran_id = f"{ad_id}-{week}-{publisher_platform}-{platform_position}"
    await db.execute(
        f'INSERT INTO "{schema}".{PLACEMENT_AD} VALUES ($1, $2, $3, $4, $5, 999, $6, $7, $8, $9)',
        ad_id,
        date.fromisoformat(week),
        fivetran_id,
        publisher_platform,
        platform_position,
        impressions,
        reach,
        spend,
        link_clicks,
    )
    child_values = {"video_play_actions": plays, "video_thruplay_watched_actions": thruplay}
    for suffix, value in child_values.items():
        if value:
            await db.execute(
                f'INSERT INTO "{schema}".{PLACEMENT_AD}_{suffix} '
                "VALUES ($1, $2, $3, 0, $4, 'video_view')",
                ad_id,
                date.fromisoformat(week),
                fivetran_id,
                value,
            )
    if video_3s_views:
        await db.execute(
            f'INSERT INTO "{schema}".{PLACEMENT_AD}_actions '
            "VALUES ($1, $2, $3, 0, $4, 'video_view')",
            ad_id,
            date.fromisoformat(week),
            fivetran_id,
            video_3s_views,
        )


async def insert_demographics(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    month: str,
    age: str,
    gender: str,
    impressions: int,
    link_clicks: int = 0,
    reach: int = 0,
    spend: float = 0.0,
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".{DEMOGRAPHICS_AD} '
        "VALUES ($1, $2, $3, $4, $5, 999, $6, $7, $8, $9)",
        ad_id,
        month,
        f"{ad_id}-{month}-{age}-{gender}",
        age,
        gender,
        impressions,
        reach,
        spend,
        link_clicks,
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


async def insert_video(
    db: Database, schema: str, *, video_id: str, source: str | None, length_s: float | None = None
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_video_history VALUES ($1, $2, NULL, $3, now())',
        video_id,
        source,
        length_s,
    )


async def insert_asset_feed_spec_video(
    db: Database,
    schema: str,
    *,
    creative_id: str,
    video_id: str,
    index: int = 0,
    label_id: str | None = None,
) -> None:
    labels = None
    if label_id:
        labels = json.dumps([{"id": label_id, "name": f"placement_asset_{label_id}"}])
    await db.execute(
        f'INSERT INTO "{schema}".{AD_ASSET_FEED_SPEC_VIDEO} VALUES ($1, $2, $3, $4, now())',
        creative_id,
        video_id,
        index,
        labels,
    )


async def insert_feed_spec_image(
    db: Database, schema: str, *, creative_id: str, index: int, hash: str, label_id: str
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".{AD_ASSET_FEED_SPEC_IMAGE} VALUES ($1, $2, $3, $4, now())',
        creative_id,
        index,
        hash,
        json.dumps([{"id": label_id, "name": f"placement_asset_{label_id}"}]),
    )


async def insert_customization_rule(
    db: Database,
    schema: str,
    *,
    creative_id: str,
    index: int,
    facebook_positions: list[str] | None,
    image_label_id: str | None = None,
    video_label_id: str | None = None,
) -> None:
    spec: dict[str, object] = {"age_min": 18}
    if facebook_positions is not None:
        spec["facebook_positions"] = facebook_positions

    def label(label_id):
        if not label_id:
            return None
        return json.dumps({"id": label_id, "name": f"placement_asset_{label_id}"})

    await db.execute(
        f'INSERT INTO "{schema}".{AD_ASSET_CUSTOMIZATION_RULE} VALUES ($1, $2, $3, $4, $5, now())',
        creative_id,
        index,
        label(image_label_id),
        label(video_label_id),
        json.dumps(spec),
    )


async def insert_library_image(db: Database, schema: str, *, hash: str, url: str) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".{AD_IMAGE_HISTORY} VALUES ($1, $2, $3, now())',
        hash,
        f"999:{hash}",
        url,
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
