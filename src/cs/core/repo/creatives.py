from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformCreative
from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_creative(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    c: PlatformCreative,
    raw_path: str,
    *,
    format_source: str,
) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline,
                              primary_text, description, cta_type, link_url, raw_path,
                              format_source, format_verified_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                CASE WHEN $12 = 'unresolved' THEN NULL ELSE now() END)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET
          format = EXCLUDED.format,
          headline = EXCLUDED.headline,
          primary_text = EXCLUDED.primary_text,
          description = EXCLUDED.description,
          cta_type = EXCLUDED.cta_type,
          link_url = EXCLUDED.link_url,
          format_source = EXCLUDED.format_source,
          format_verified_at = EXCLUDED.format_verified_at,
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
        format_source,
    )
    return row["id"], row["created"]


async def set_creative_format(
    db: Database, org_id: UUID, creative_id: UUID, format: str, format_source: str
) -> None:
    await db.execute(
        "UPDATE creative SET format=$3, format_source=$4,"
        " format_verified_at = CASE WHEN $4 = 'unresolved' THEN NULL ELSE now() END"
        " WHERE org_id=$1 AND id=$2",
        org_id,
        creative_id,
        format,
        format_source,
    )


async def primary_asset_kind(
    db: Database, ad_account_id: UUID, platform_creative_id: str
) -> str | None:
    """Kind of the stored creative's primary file, or None (no file, or not stored yet)."""
    return await db.fetchval(
        "SELECT a.kind FROM creative c JOIN asset a ON a.id = c.primary_asset_id"
        " WHERE c.ad_account_id=$1 AND c.platform_creative_id=$2",
        ad_account_id,
        platform_creative_id,
    )


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
    duration_range_ms: tuple[int, int | None] | None = None,
) -> asyncpg.Record:
    """Pooled sums over every *other* creative of the same format in the account. With
    `duration_range_ms` (low exclusive, high inclusive, None = open), only creatives whose
    primary asset length falls in that range; creatives of unknown length are left out."""
    lo, hi = duration_range_ms if duration_range_ms is not None else (None, None)
    return await db.fetchrow(
        f"SELECT {_SUMS_SQL}, count(DISTINCT m.creative_id) AS creatives "
        "FROM metric_daily m JOIN creative c ON c.id = m.creative_id "
        "LEFT JOIN asset a ON a.id = c.primary_asset_id "
        "WHERE m.org_id=$1 AND m.ad_account_id=$2 AND c.format=$3 AND m.creative_id <> $4 "
        "AND m.day BETWEEN $5 AND $6 "
        "AND ($7::bigint IS NULL OR a.duration_ms > $7) "
        "AND ($8::bigint IS NULL OR a.duration_ms <= $8)",
        org_id,
        ad_account_id,
        fmt,
        exclude_creative_id,
        since,
        until,
        lo,
        hi,
    )


_PLACEMENT_SUMS_SQL = """
    sum(impressions) AS impressions, sum(reach) AS reach, sum(spend) AS spend,
    sum(link_clicks) AS link_clicks, sum(video_3s_views) AS video_3s_views,
    sum(video_plays) AS video_plays, sum(video_thruplay) AS video_thruplay,
    sum(video_p25) AS video_p25, sum(video_p50) AS video_p50, sum(video_p75) AS video_p75,
    sum(video_p100) AS video_p100, sum(purchases) AS purchases
"""


# Placement rows are weekly and the funnel window is 30 days, so a week is counted when it
# overlaps the window at all. Both the creative and its baseline use the same rule, so the
# comparison stays like-for-like even where a boundary week reaches outside the window.
def _week_overlap(since: int, until: int) -> str:
    return f"p.week <= ${until} AND p.week + 6 >= ${since}"


async def creative_placement_sums(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        f"SELECT p.publisher_platform, p.platform_position, {_PLACEMENT_SUMS_SQL} "
        "FROM metric_placement p "
        f"WHERE p.org_id=$1 AND p.creative_id=$2 AND {_week_overlap(3, 4)} "
        "GROUP BY 1, 2",
        org_id,
        creative_id,
        since,
        until,
    )


async def baseline_placement_sums(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    fmt: str,
    exclude_creative_id: UUID,
    since: date,
    until: date,
) -> list[asyncpg.Record]:
    """The account's per-placement totals over every *other* creative of the same format."""
    return await db.fetch(
        f"SELECT p.publisher_platform, p.platform_position, {_PLACEMENT_SUMS_SQL} "
        "FROM metric_placement p JOIN creative c ON c.id = p.creative_id "
        "WHERE p.org_id=$1 AND p.ad_account_id=$2 AND c.format=$3 AND p.creative_id <> $6 "
        f"AND {_week_overlap(4, 5)} GROUP BY 1, 2",
        org_id,
        ad_account_id,
        fmt,
        since,
        until,
        exclude_creative_id,
    )


# Audience rows are monthly; any month touching the window counts, on both sides.
def _month_overlap(since: int, until: int) -> str:
    return f"a.month <= ${until} AND (a.month + INTERVAL '1 month' - INTERVAL '1 day') >= ${since}"


async def creative_audience_sums(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT a.age, a.gender, sum(a.impressions) AS impressions FROM metric_audience a "
        f"WHERE a.org_id=$1 AND a.creative_id=$2 AND {_month_overlap(3, 4)} GROUP BY 1, 2",
        org_id,
        creative_id,
        since,
        until,
    )


async def baseline_audience_sums(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    fmt: str,
    exclude_creative_id: UUID,
    since: date,
    until: date,
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT a.age, a.gender, sum(a.impressions) AS impressions FROM metric_audience a "
        "JOIN creative c ON c.id = a.creative_id "
        "WHERE a.org_id=$1 AND a.ad_account_id=$2 AND c.format=$3 AND a.creative_id <> $6 "
        f"AND {_month_overlap(4, 5)} GROUP BY 1, 2",
        org_id,
        ad_account_id,
        fmt,
        since,
        until,
        exclude_creative_id,
    )


async def creative_first_metric_day(db: Database, org_id: UUID, creative_id: UUID) -> date | None:
    return await db.fetchval(
        "SELECT min(day) FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND impressions > 0",
        org_id,
        creative_id,
    )
