"""The Apify ledger: one paid lookup per creative, ever (db migration 20260921000003)."""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database


async def has_video_resolution(db: Database, creative_id: UUID) -> bool:
    return bool(
        await db.fetchval("SELECT 1 FROM video_resolution WHERE creative_id = $1", creative_id)
    )


async def start_video_resolution(
    db: Database, org_id: UUID, creative_id: UUID, source: str
) -> bool:
    """Claim the one lookup this creative gets. False when it was already claimed, by this
    job's earlier attempt or any other: the caller must not call Apify."""
    return bool(
        await db.fetchval(
            """
            INSERT INTO video_resolution (creative_id, org_id, source, status)
            VALUES ($1, $2, $3, 'started')
            ON CONFLICT (creative_id) DO NOTHING
            RETURNING 1
            """,
            creative_id,
            org_id,
            source,
        )
    )


async def finish_video_resolution(
    db: Database, creative_id: UUID, *, video_url: str | None, error: str | None = None
) -> None:
    await db.execute(
        """
        UPDATE video_resolution
        SET status = CASE WHEN $2::text IS NULL THEN 'failed' ELSE 'found' END,
            video_url = $2, error = $3, finished_at = now()
        WHERE creative_id = $1
        """,
        creative_id,
        video_url,
        (error or "")[:500] or None,
    )
