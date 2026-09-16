from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id
from cs.tagging.taxonomy import Taxonomy


async def ensure_taxonomy_version(db: Database, taxonomy: Taxonomy) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, $2, $3)
        ON CONFLICT (yaml_sha256) DO UPDATE SET version = EXCLUDED.version RETURNING id
        """,
        new_id(),
        taxonomy.version,
        taxonomy.sha256,
    )
    return row["id"]


async def start_tag_run(
    db: Database, org_id: UUID, creative_id: UUID, taxonomy_version_id: UUID, model: str
) -> UUID | None:
    existing = await db.fetchrow(
        "SELECT id, status FROM tag_run "
        "WHERE creative_id=$1 AND taxonomy_version_id=$2 AND model=$3",
        creative_id,
        taxonomy_version_id,
        model,
    )
    if existing and existing["status"] == "done":
        return None
    if existing:
        await db.execute(
            "UPDATE tag_run SET status='running', error=NULL WHERE id=$1", existing["id"]
        )
        await db.execute("DELETE FROM tag WHERE tag_run_id=$1", existing["id"])
        return existing["id"]
    rid = new_id()
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status) "
        "VALUES ($1,$2,$3,$4,$5,'running')",
        rid,
        org_id,
        creative_id,
        taxonomy_version_id,
        model,
    )
    return rid


async def finish_tag_run(
    db: Database,
    org_id: UUID,
    run_id: UUID,
    tags: list[dict],
    prompt_sha: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    creative_id = await db.fetchval("SELECT creative_id FROM tag_run WHERE id=$1", run_id)
    async with db.transaction() as tx:
        await tx.execute("DELETE FROM tag WHERE tag_run_id=$1", run_id)
        for t in tags:
            await tx.execute(
                "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, "
                "confidence, evidence, kind, raw_value, t_start_s, ordinal) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
                new_id(),
                org_id,
                run_id,
                creative_id,
                t["dimension"],
                t["value"],
                t["confidence"],
                t["evidence"],
                t.get("kind", "single"),
                t.get("raw_value"),
                t.get("t_start_s"),
                t.get("ordinal"),
            )
        await tx.execute(
            "UPDATE tag_run SET status='done', prompt_sha256=$2, input_tokens=$3, "
            "output_tokens=$4, finished_at=now() WHERE id=$1",
            run_id,
            prompt_sha,
            input_tokens,
            output_tokens,
        )


async def fail_tag_run(db: Database, org_id: UUID, run_id: UUID, error: str) -> None:
    await db.execute(
        "UPDATE tag_run SET status='failed', error=$2, finished_at=now() WHERE id=$1",
        run_id,
        error[:2000],
    )


async def latest_run_id(db: Database, org_id: UUID, creative_id: UUID) -> UUID | None:
    return await db.fetchval(
        "SELECT id FROM tag_run WHERE org_id=$1 AND creative_id=$2 AND status='done' "
        "ORDER BY finished_at DESC LIMIT 1",
        org_id,
        creative_id,
    )


async def latest_tags(db: Database, org_id: UUID, creative_id: UUID) -> list[asyncpg.Record]:
    rid = await latest_run_id(db, org_id, creative_id)
    if rid is None:
        return []
    return await db.fetch(
        "SELECT dimension, value, confidence, evidence, kind, raw_value, t_start_s, ordinal "
        "FROM tag WHERE tag_run_id=$1 ORDER BY dimension, ordinal, t_start_s, value",
        rid,
    )


async def latest_tag_value(
    db: Database, org_id: UUID, creative_id: UUID, dimension: str
) -> str | None:
    rid = await latest_run_id(db, org_id, creative_id)
    if rid is None:
        return None
    return await db.fetchval(
        "SELECT value FROM tag WHERE tag_run_id=$1 AND dimension=$2 "
        "ORDER BY ordinal, t_start_s, value LIMIT 1",
        rid,
        dimension,
    )
