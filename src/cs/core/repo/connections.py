from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


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
