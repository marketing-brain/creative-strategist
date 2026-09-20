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


async def twin_tag_run(
    db: Database, org_id: UUID, creative_id: UUID, taxonomy_version_id: UUID, model: str
) -> UUID | None:
    """A finished run, under this taxonomy and model, on another creative whose model input
    was identical: same format, byte-identical primary file, and the same copy, since the
    copy is part of the tagging prompt. Same file with different copy is a different input
    and is tagged on its own. Carousels are excluded: their input is every card, and only
    the first is the primary file."""
    return await db.fetchval(
        """
        SELECT tr.id FROM creative c
        JOIN asset a ON a.id = c.primary_asset_id
        JOIN creative peer ON peer.org_id = c.org_id AND peer.id <> c.id
                          AND peer.format = c.format
                          AND (peer.headline, peer.primary_text, peer.description,
                               peer.cta_type, peer.link_url)
                              IS NOT DISTINCT FROM
                              (c.headline, c.primary_text, c.description, c.cta_type, c.link_url)
        JOIN asset pa ON pa.id = peer.primary_asset_id AND pa.sha256 = a.sha256
        JOIN tag_run tr ON tr.creative_id = peer.id AND tr.status = 'done'
                       AND tr.taxonomy_version_id = $3 AND tr.model = $4
        WHERE c.org_id = $1 AND c.id = $2 AND c.format IN ('image', 'video')
        ORDER BY tr.finished_at DESC LIMIT 1
        """,
        org_id,
        creative_id,
        taxonomy_version_id,
        model,
    )


async def copy_tag_run(
    db: Database, org_id: UUID, run_id: UUID, source_run_id: UUID
) -> list[asyncpg.Record]:
    """Finish `run_id` with the source run's tags instead of a model call, and return them.
    The run keeps the source's prompt hash, since that is the prompt its tags came from, and
    records zero tokens, since it spent none."""
    source = await db.fetchrow("SELECT prompt_sha256 FROM tag_run WHERE id=$1", source_run_id)
    rows = await db.fetch(
        "SELECT dimension, value, confidence, evidence, kind, raw_value, t_start_s, ordinal "
        "FROM tag WHERE tag_run_id=$1",
        source_run_id,
    )
    await finish_tag_run(db, org_id, run_id, [dict(r) for r in rows], source["prompt_sha256"], 0, 0)
    return rows


async def footage_latest_tags(
    db: Database, org_id: UUID, creative_ids: list[UUID]
) -> list[asyncpg.Record]:
    """Tags for a footage group: one run stands for the file, the latest done run of the
    highest-spend copy that has one, v2 runs first. This is the account read's rule
    (`account_read.units`), on purpose: the page sets these tags beside account patterns
    whose `has` flag was judged on that copy's run. Copies of one file are tagged
    separately and can disagree, so any other pick lets the page show "face to camera"
    above a pattern row that says the ad lacks it."""
    rid = await db.fetchval(
        """
        SELECT r.id
        FROM tag_run r
        JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
        LEFT JOIN (SELECT creative_id, sum(spend) AS spend FROM metric_daily
                   WHERE org_id = $1 AND creative_id = ANY($2) GROUP BY 1) m
               ON m.creative_id = r.creative_id
        WHERE r.org_id = $1 AND r.creative_id = ANY($2) AND r.status = 'done'
        ORDER BY (split_part(v.version, '.', 1)::int >= 2) DESC,
                 coalesce(m.spend, 0) DESC, r.creative_id DESC, r.finished_at DESC
        LIMIT 1
        """,
        org_id,
        creative_ids,
    )
    if rid is None:
        return []
    return await _run_tags(db, rid)


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
    return await _run_tags(db, rid)


async def _run_tags(db: Database, run_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT dimension, value, confidence, evidence, kind, raw_value, t_start_s, ordinal "
        "FROM tag WHERE tag_run_id=$1 ORDER BY dimension, ordinal, t_start_s, value",
        run_id,
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
