from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


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


async def set_asset_duration(db: Database, org_id: UUID, asset_id: UUID, duration_ms: int) -> None:
    await db.execute(
        "UPDATE asset SET duration_ms = $3 WHERE org_id = $1 AND id = $2",
        org_id,
        asset_id,
        duration_ms,
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
