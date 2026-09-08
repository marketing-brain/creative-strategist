from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core.db import Database
from cs.core.ids import new_id
from cs.ingest.partitions import ensure_metric_partition

METRIC_CHUNK_SIZE = 1000

_UPSERT_METRIC_SQL = """
    INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement,
        spend, impressions, clicks, link_clicks, conversions, conversion_value,
        video_3s_views, video_thruplay, video_p50, video_p100, raw)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
        spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
        link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
        conversion_value=EXCLUDED.conversion_value,
        video_3s_views=EXCLUDED.video_3s_views, video_thruplay=EXCLUDED.video_thruplay,
        video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100, raw=EXCLUDED.raw
"""


async def upsert_ad_account(
    db: Database,
    org_id: UUID,
    platform_account_id: str,
    name: str,
    currency: str,
    meta_connection_id: UUID | None = None,
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, currency,
                                meta_connection_id, status)
        VALUES ($1, $2, 'meta', $3, $4, $5, $6, 'paused')
        ON CONFLICT (org_id, platform, platform_account_id) DO UPDATE
          SET name = EXCLUDED.name, currency = EXCLUDED.currency,
              meta_connection_id = EXCLUDED.meta_connection_id
        RETURNING id
        """,
        new_id(),
        org_id,
        platform_account_id,
        name,
        currency,
        meta_connection_id,
    )
    return row["id"]


async def list_ad_accounts(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT a.id, a.platform, a.platform_account_id, a.name, a.currency, a.status,
               a.sync_cursor, a.created_at,
               c.sync_status AS connection_status, c.last_error AS connection_error
        FROM ad_account a LEFT JOIN meta_connection c ON c.id = a.meta_connection_id
        WHERE a.org_id = $1 ORDER BY a.created_at
        """,
        org_id,
    )


async def list_active_ad_account_ids(db: Database, org_id: UUID) -> set[UUID]:
    """Accounts the user has explicitly activated -- the consent boundary the Fivetran
    transform gates ingestion on. A discovered-but-paused account gets no ads, creatives,
    assets, or tagging until it appears here."""
    rows = await db.fetch("SELECT id FROM ad_account WHERE org_id=$1 AND status='active'", org_id)
    return {r["id"] for r in rows}


async def get_ad_account(db: Database, org_id: UUID, account_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM ad_account WHERE org_id = $1 AND id = $2", org_id, account_id
    )


async def set_ad_account_status(db: Database, org_id: UUID, account_id: UUID, status: str) -> None:
    await db.execute(
        "UPDATE ad_account SET status = $3 WHERE org_id = $1 AND id = $2",
        org_id,
        account_id,
        status,
    )


async def upsert_meta_connection(
    db: Database, org_id: UUID, fivetran_connector_id: str, schema_name: str
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO meta_connection (id, org_id, fivetran_connector_id, schema_name, sync_status)
        VALUES ($1, $2, $3, $4, 'pending')
        ON CONFLICT (org_id) DO UPDATE
          SET fivetran_connector_id = EXCLUDED.fivetran_connector_id,
              schema_name = EXCLUDED.schema_name, sync_status = 'pending', last_error = NULL
        RETURNING id
        """,
        new_id(),
        org_id,
        fivetran_connector_id,
        schema_name,
    )
    return row["id"]


async def get_meta_connection(db: Database, org_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM meta_connection WHERE org_id = $1", org_id)


async def get_meta_connection_by_id(db: Database, connection_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM meta_connection WHERE id = $1", connection_id)


async def set_meta_connection_status(
    db: Database,
    connection_id: UUID,
    status: str,
    *,
    last_synced_at: datetime | None = None,
    last_error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE meta_connection SET sync_status=$2, last_synced_at=COALESCE($3, last_synced_at), "
        "last_error=$4 WHERE id=$1",
        connection_id,
        status,
        last_synced_at,
        last_error,
    )


async def list_ready_meta_connections_with_active_account(db: Database) -> list[asyncpg.Record]:
    """Connections eligible for a nightly re-sync: healthy, and at least one account someone
    has activated -- mirrors the old nightly_sync's `WHERE status = 'active'` filter, just
    re-expressed at the connection level since discovery now happens per-org, not per-account."""
    return await db.fetch(
        """
        SELECT DISTINCT c.id, c.org_id, c.schema_name FROM meta_connection c
        JOIN ad_account a ON a.meta_connection_id = c.id
        WHERE c.sync_status = 'ready' AND a.status = 'active'
        """
    )


async def upsert_creative(
    db: Database, org_id: UUID, ad_account_id: UUID, c: PlatformCreative, raw_path: str
) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline,
                              primary_text, description, cta_type, link_url, raw_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET last_seen_at = now()
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


async def upsert_ad(
    db: Database, org_id: UUID, ad_account_id: UUID, a: PlatformAd, creative_id: UUID
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad (id, org_id, ad_account_id, platform_ad_id, creative_id, adset_id,
                        campaign_id, name, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (ad_account_id, platform_ad_id) DO UPDATE
          SET status = EXCLUDED.status, name = EXCLUDED.name, creative_id = EXCLUDED.creative_id,
              last_seen_at = now()
        RETURNING id
        """,
        new_id(),
        org_id,
        ad_account_id,
        a.platform_ad_id,
        creative_id,
        a.adset_id,
        a.campaign_id,
        a.name,
        a.status,
    )
    return row["id"]


async def insert_asset(
    db: Database,
    org_id: UUID,
    creative_id: UUID,
    kind: str,
    path: str,
    sha256: str,
    mime: str,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
    seq: int = 0,
) -> UUID:
    aid = new_id()
    await db.execute(
        "INSERT INTO asset (id, org_id, creative_id, kind, path, sha256, mime, width, height, "
        "duration_ms, seq) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        aid,
        org_id,
        creative_id,
        kind,
        path,
        sha256,
        mime,
        width,
        height,
        duration_ms,
        seq,
    )
    return aid


async def set_primary_asset(db: Database, org_id: UUID, creative_id: UUID, asset_id: UUID) -> None:
    await db.execute(
        "UPDATE creative SET primary_asset_id = $3 WHERE org_id = $1 AND id = $2",
        org_id,
        creative_id,
        asset_id,
    )


async def delete_assets_by_kind(
    db: Database, org_id: UUID, creative_id: UUID, kinds: tuple[str, ...]
) -> None:
    await db.execute(
        "DELETE FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind = ANY($3)",
        org_id,
        creative_id,
        list(kinds),
    )


async def creative_has_asset(db: Database, org_id: UUID, creative_id: UUID, kind: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 LIMIT 1",
            org_id,
            creative_id,
            kind,
        )
    )


async def creative_has_primary_asset(db: Database, org_id: UUID, creative_id: UUID) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM creative WHERE org_id=$1 AND id=$2 AND primary_asset_id IS NOT NULL",
            org_id,
            creative_id,
        )
    )


async def get_asset_by_seq(
    db: Database, org_id: UUID, creative_id: UUID, kind: str, seq: int
) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 AND seq=$4",
        org_id,
        creative_id,
        kind,
        seq,
    )


async def list_assets_by_kind(
    db: Database, org_id: UUID, creative_id: UUID, kind: str
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 ORDER BY seq",
        org_id,
        creative_id,
        kind,
    )


async def upsert_metrics(
    db: Database, org_id: UUID, ad_account_id: UUID, rows: list[MetricRow]
) -> tuple[int, int]:
    """Upsert metric rows, returning (written, skipped).

    Rows whose ad is unknown to us are skipped: Meta insights can name ads that the ad
    listing did not return (deleted or out of the page window).
    """
    if not rows:
        return 0, 0
    ads = await db.fetch(
        "SELECT id, platform_ad_id, creative_id FROM ad WHERE org_id=$1 AND ad_account_id=$2",
        org_id,
        ad_account_id,
    )
    by_pid = {a["platform_ad_id"]: a for a in ads}
    params: list[tuple] = []
    skipped = 0
    for r in rows:
        a = by_pid.get(r.platform_ad_id)
        if not a:
            skipped += 1
            continue
        params.append(
            (
                org_id,
                ad_account_id,
                a["creative_id"],
                a["id"],
                r.day,
                r.placement,
                r.spend,
                r.impressions,
                r.clicks,
                r.link_clicks,
                r.conversions,
                r.conversion_value,
                r.video_3s_views,
                r.video_thruplay,
                r.video_p50,
                r.video_p100,
                r.raw,
            )
        )
    if not params:
        return 0, skipped
    for month in {t[4].replace(day=1) for t in params}:
        await ensure_metric_partition(db, month)
    for i in range(0, len(params), METRIC_CHUNK_SIZE):
        async with db.transaction() as tx:
            await tx.executemany(_UPSERT_METRIC_SQL, params[i : i + METRIC_CHUNK_SIZE])
    return len(params), skipped


async def list_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT c.id, c.ad_account_id, c.platform_creative_id, c.format, c.headline, c.primary_text,
               c.cta_type, c.link_url, c.first_seen_at, c.last_seen_at,
               a.path AS primary_asset_path, a.mime AS primary_asset_mime
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
        ORDER BY c.first_seen_at DESC LIMIT $4 OFFSET $5
        """,
        org_id,
        ad_account_id,
        format,
        limit,
        offset,
    )


async def count_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
) -> int:
    return await db.fetchval(
        """
        SELECT count(*) FROM creative c
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
        """,
        org_id,
        ad_account_id,
        format,
    )


async def get_creative(db: Database, org_id: UUID, creative_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime
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
