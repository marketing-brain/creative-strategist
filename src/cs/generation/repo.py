from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def create_generation(
    db: Database,
    org_id: UUID,
    brand_kit_id: UUID,
    kind: str,
    variants: int,
    created_by: str,
    source_insight_id: UUID | None = None,
    source_creative_ids: tuple[UUID, ...] = (),
    concept: str | None = None,
) -> UUID:
    gid = new_id()
    await db.execute(
        "INSERT INTO generation (id, org_id, brand_kit_id, source_insight_id, source_creative_ids, "
        "concept, kind, variants, status, created_by) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending',$9)",
        gid,
        org_id,
        brand_kit_id,
        source_insight_id,
        list(source_creative_ids),
        concept,
        kind,
        variants,
        created_by,
    )
    return gid


async def set_generation_status(
    db: Database,
    org_id: UUID,
    gid: UUID,
    status: str,
    error: str | None = None,
    model: str | None = None,
    prompt: str | None = None,
) -> None:
    await db.execute(
        "UPDATE generation SET status=$3, error=$4, model=coalesce($5, model), "
        "prompt=coalesce($6, prompt), finished_at = CASE WHEN $3 IN ('done','failed') THEN now() "
        "ELSE finished_at END WHERE org_id=$1 AND id=$2",
        org_id,
        gid,
        status,
        error,
        model,
        prompt,
    )


async def get_generation(db: Database, org_id: UUID, gid: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM generation WHERE org_id=$1 AND id=$2", org_id, gid)


async def list_generations(db: Database, org_id: UUID, limit: int = 50) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM generation WHERE org_id=$1 ORDER BY created_at DESC LIMIT $2", org_id, limit
    )


async def insert_generated_asset(
    db: Database,
    org_id: UUID,
    gid: UUID,
    kind: str,
    aspect: str,
    variant: int,
    path: str,
    sha256: str,
    headline: str,
    primary_text: str,
    cta_type: str,
) -> UUID:
    aid = new_id()
    await db.execute(
        "INSERT INTO generated_asset (id, generation_id, org_id, kind, aspect, variant, path, "
        "sha256, headline, primary_text, cta_type) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        aid,
        gid,
        org_id,
        kind,
        aspect,
        variant,
        path,
        sha256,
        headline,
        primary_text,
        cta_type,
    )
    return aid


async def delete_generated_assets(db: Database, org_id: UUID, generation_id: UUID) -> None:
    """Delete all generated_asset rows for a generation.

    Called at the top of a generation.run attempt so a job-queue retry after a partial
    failure regenerates from scratch instead of piling duplicate drafts (and duplicate spend)
    on top of whatever an earlier, failed attempt already inserted.
    """
    await db.execute(
        "DELETE FROM generated_asset WHERE org_id=$1 AND generation_id=$2", org_id, generation_id
    )


async def get_generated_asset_by_variant(
    db: Database, org_id: UUID, generation_id: UUID, kind: str, aspect: str, variant: int
) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM generated_asset WHERE org_id=$1 AND generation_id=$2 AND kind=$3 "
        "AND aspect=$4 AND variant=$5",
        org_id,
        generation_id,
        kind,
        aspect,
        variant,
    )


async def list_generated_assets(
    db: Database,
    org_id: UUID,
    generation_id: UUID | None = None,
    review_state: str | None = None,
    limit: int = 100,
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM generated_asset WHERE org_id=$1 AND ($2::uuid IS NULL OR generation_id=$2) "
        "AND ($3::text IS NULL OR review_state=$3) ORDER BY created_at, variant, aspect LIMIT $4",
        org_id,
        generation_id,
        review_state,
        limit,
    )


async def get_generated_asset(db: Database, org_id: UUID, aid: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM generated_asset WHERE org_id=$1 AND id=$2", org_id, aid)


async def set_review_state(db: Database, org_id: UUID, aid: UUID, state: str, user_id: str) -> bool:
    res = await db.execute(
        "UPDATE generated_asset SET review_state=$3, reviewed_by=$4, reviewed_at=now() "
        "WHERE org_id=$1 AND id=$2 AND review_state='draft'",
        org_id,
        aid,
        state,
        user_id,
    )
    return res.endswith("1")
