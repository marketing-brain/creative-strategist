from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformCreative
from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_creative(
    db: Database, org_id: UUID, ad_account_id: UUID, c: PlatformCreative, raw_path: str
) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline,
                              primary_text, description, cta_type, link_url, raw_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET
          format = EXCLUDED.format,
          headline = EXCLUDED.headline,
          primary_text = EXCLUDED.primary_text,
          description = EXCLUDED.description,
          cta_type = EXCLUDED.cta_type,
          link_url = EXCLUDED.link_url,
          last_seen_at = now()
        RETURNING id, (xmax = 0) AS created
        """,
        new_id(),
        org_id,
        ad_account_id,
        c.platform_creative_id,
        c.format,
        c.headline,
        c.primary_text,
        c.description,
        c.cta_type,
        c.link_url,
        raw_path,
    )
    return row["id"], row["created"]


def _tag_filter_sql(dim_param: str, value_param: str) -> str:
    return f"""
      AND ({dim_param}::text IS NULL OR EXISTS (
            SELECT 1 FROM tag t
            WHERE t.tag_run_id = (
                SELECT id FROM tag_run tr
                WHERE tr.creative_id = c.id AND tr.status = 'done'
                ORDER BY tr.finished_at DESC LIMIT 1
            ) AND t.dimension = {dim_param} AND t.value = {value_param}
          ))
"""


async def list_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    limit: int = 50,
    offset: int = 0,
    dimension: str | None = None,
    value: str | None = None,
) -> list[asyncpg.Record]:
    return await db.fetch(
        f"""
        SELECT c.id, c.ad_account_id, c.platform_creative_id, c.format, c.headline, c.primary_text,
               c.cta_type, c.link_url, c.first_seen_at, c.last_seen_at,
               a.path AS primary_asset_path, a.mime AS primary_asset_mime,
               COALESCE(m.spend, 0) AS spend_30d, m.impressions AS impressions_30d,
               m.clicks AS clicks_30d, m.conversions AS conversions_30d,
               m.conversion_value AS conversion_value_30d
        FROM creative c
        LEFT JOIN asset a ON a.id = c.primary_asset_id
        LEFT JOIN (
            SELECT creative_id, sum(spend) AS spend, sum(impressions) AS impressions,
                   sum(clicks) AS clicks, sum(conversions) AS conversions,
                   sum(conversion_value) AS conversion_value
            FROM metric_daily
            WHERE org_id = $1 AND day >= current_date - interval '30 days'
            GROUP BY creative_id
        ) m ON m.creative_id = c.id
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
          {_tag_filter_sql("$6", "$7")}
        ORDER BY c.first_seen_at DESC LIMIT $4 OFFSET $5
        """,
        org_id,
        ad_account_id,
        format,
        limit,
        offset,
        dimension,
        value,
    )


async def count_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
) -> int:
    return await db.fetchval(
        f"""
        SELECT count(*) FROM creative c
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
          {_tag_filter_sql("$4", "$5")}
        """,
        org_id,
        ad_account_id,
        format,
        dimension,
        value,
    )


async def get_creative(db: Database, org_id: UUID, creative_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime,
               a.duration_ms AS primary_asset_duration_ms
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND c.id = $2
        """,
        org_id,
        creative_id,
    )


async def creative_metrics(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT day, sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
               sum(link_clicks) AS link_clicks, sum(conversions) AS conversions,
               sum(conversion_value) AS conversion_value, sum(video_3s_views) AS video_3s_views,
               sum(video_thruplay) AS video_thruplay
        FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND day BETWEEN $3 AND $4
        GROUP BY day ORDER BY day
        """,
        org_id,
        creative_id,
        since,
        until,
    )


_SUMS_SQL = """
    sum(impressions) AS impressions, sum(reach) AS reach, sum(link_clicks) AS link_clicks,
    sum(spend) AS spend, sum(video_3s_views) AS video_3s_views, sum(video_plays) AS video_plays,
    sum(video_p25) AS video_p25, sum(video_p50) AS video_p50, sum(video_p75) AS video_p75,
    sum(video_p95) AS video_p95, sum(video_p100) AS video_p100,
    sum(video_thruplay) AS video_thruplay, sum(video_watch_seconds) AS video_watch_seconds,
    sum(landing_page_views) AS landing_page_views, sum(content_views) AS content_views,
    sum(add_to_carts) AS add_to_carts, sum(checkouts) AS checkouts, sum(purchases) AS purchases,
    sum(post_reactions) AS post_reactions, sum(comments) AS comments, sum(saves) AS saves,
    sum(conversions) AS conversions, sum(conversion_value) AS conversion_value
"""


async def creative_window_sums(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> asyncpg.Record:
    """Always returns one row; every sum is NULL when no rows fall in the window."""
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL} FROM metric_daily "
        "WHERE org_id=$1 AND creative_id=$2 AND day BETWEEN $3 AND $4",
        org_id,
        creative_id,
        since,
        until,
    )


async def baseline_window_sums(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    fmt: str,
    exclude_creative_id: UUID,
    since: date,
    until: date,
) -> asyncpg.Record:
    """Pooled sums over every *other* creative of the same format in the account."""
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL}, count(DISTINCT m.creative_id) AS creatives "
        "FROM metric_daily m JOIN creative c ON c.id = m.creative_id "
        "WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3 AND m.creative_id <> $4 "
        "AND m.day BETWEEN $5 AND $6",
        org_id,
        ad_account_id,
        fmt,
        exclude_creative_id,
        since,
        until,
    )


async def creative_first_metric_day(db: Database, org_id: UUID, creative_id: UUID) -> date | None:
    return await db.fetchval(
        "SELECT min(day) FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND impressions > 0",
        org_id,
        creative_id,
    )
