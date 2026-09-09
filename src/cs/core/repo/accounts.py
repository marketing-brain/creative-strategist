from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


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
