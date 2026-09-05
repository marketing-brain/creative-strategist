from __future__ import annotations

from datetime import datetime
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
    token_enc: str,
    expires_at: datetime | None,
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, currency,
                                access_token_enc, token_expires_at, status)
        VALUES ($1, $2, 'meta', $3, $4, $5, $6, $7, 'paused')
        ON CONFLICT (org_id, platform, platform_account_id) DO UPDATE
          SET name = EXCLUDED.name, currency = EXCLUDED.currency,
              access_token_enc = EXCLUDED.access_token_enc,
              token_expires_at = EXCLUDED.token_expires_at
        RETURNING id
        """,
        new_id(),
        org_id,
        platform_account_id,
        name,
        currency,
        token_enc,
        expires_at,
    )
    return row["id"]


async def list_ad_accounts(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT id, platform, platform_account_id, name, currency, status, sync_cursor, created_at "
        "FROM ad_account WHERE org_id = $1 ORDER BY created_at",
        org_id,
    )


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
