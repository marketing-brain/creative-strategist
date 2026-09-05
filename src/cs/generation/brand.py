from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


@dataclass
class BrandKit:
    id: UUID
    org_id: UUID
    version: int
    colors: list[str]
    fonts: list[str]
    logo_path: str | None
    logo_sha256: str | None
    voice_rules: str
    prohibited_claims: list[str]


def _from_row(r) -> BrandKit:
    return BrandKit(r["id"], r["org_id"], r["version"], list(r["colors"]), list(r["fonts"]), r["logo_path"],
                    r["logo_sha256"], r["voice_rules"], list(r["prohibited_claims"]))


async def create_brand_kit(db: Database, org_id: UUID, colors: list[str], fonts: list[str], voice_rules: str,
                           prohibited_claims: list[str], logo_path: str | None = None, logo_sha256: str | None = None) -> BrandKit:
    async with db.transaction() as tx:
        version = (await tx.fetchval("SELECT coalesce(max(version), 0) FROM brand_kit WHERE org_id=$1", org_id)) + 1
        kid = new_id()
        row = await tx.fetchrow(
            "INSERT INTO brand_kit (id, org_id, version, colors, fonts, logo_path, logo_sha256, voice_rules, prohibited_claims) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING *",
            kid, org_id, version, colors, fonts, logo_path, logo_sha256, voice_rules, prohibited_claims)
        await tx.execute("UPDATE org SET active_brand_kit_id=$2 WHERE id=$1", org_id, kid)
    return _from_row(row)


async def get_active_brand_kit(db: Database, org_id: UUID) -> BrandKit | None:
    row = await db.fetchrow("SELECT k.* FROM org o JOIN brand_kit k ON k.id = o.active_brand_kit_id WHERE o.id=$1", org_id)
    return _from_row(row) if row else None


async def get_brand_kit(db: Database, org_id: UUID, kit_id: UUID) -> BrandKit | None:
    row = await db.fetchrow("SELECT * FROM brand_kit WHERE org_id=$1 AND id=$2", org_id, kit_id)
    return _from_row(row) if row else None
