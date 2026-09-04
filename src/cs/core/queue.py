from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


@dataclass
class Job:
    id: UUID
    org_id: UUID
    job_type: str
    payload: dict
    attempts: int
    max_attempts: int


async def enqueue(
    db: Database,
    org_id: UUID,
    job_type: str,
    payload: dict | None = None,
    *,
    dedup_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> UUID | None:
    jid = new_id()
    row = await db.fetchrow(
        """
        INSERT INTO job (id, org_id, job_type, dedup_key, payload, status, run_after, max_attempts)
        VALUES ($1, $2, $3, $4, $5, 'pending', COALESCE($6, now()), $7)
        ON CONFLICT (org_id, job_type, dedup_key)
          WHERE status IN ('pending','running') AND dedup_key IS NOT NULL
        DO NOTHING
        RETURNING id
        """,
        jid,
        org_id,
        job_type,
        dedup_key,
        payload or {},
        run_after,
        max_attempts,
    )
    return row["id"] if row else None


async def claim(db: Database, worker_id: str, job_types: list[str] | None = None) -> Job | None:
    row = await db.fetchrow(
        """
        WITH next AS (
          SELECT id FROM job
          WHERE status = 'pending' AND run_after <= now()
            AND ($1::text[] IS NULL OR job_type = ANY($1))
          ORDER BY run_after, id
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE job SET status='running', locked_by=$2, locked_at=now(), attempts = attempts + 1
        WHERE id = (SELECT id FROM next)
        RETURNING id, org_id, job_type, payload, attempts, max_attempts
        """,
        job_types,
        worker_id,
    )
    if not row:
        return None
    return Job(
        row["id"],
        row["org_id"],
        row["job_type"],
        row["payload"],
        row["attempts"],
        row["max_attempts"],
    )


async def complete(db: Database, job_id: UUID) -> None:
    await db.execute(
        "UPDATE job SET status='done', finished_at=now(), locked_by=NULL"
        " WHERE id=$1 AND status='running'",
        job_id,
    )


async def fail(db: Database, job_id: UUID, error: str) -> None:
    await db.execute(
        """
        UPDATE job SET
          error = $2,
          locked_by = NULL,
          status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
          finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
          run_after = now() + make_interval(secs => LEAST(power(2, attempts), 300))
        WHERE id = $1 AND status = 'running'
        """,
        job_id,
        error[:4000],
    )


async def reclaim_stale(db: Database, lease_seconds: int) -> int:
    result = await db.execute(
        """
        UPDATE job SET status='pending', locked_by=NULL, locked_at=NULL
        WHERE status='running' AND locked_at < now() - make_interval(secs => $1)
        """,
        lease_seconds,
    )
    return int(result.split()[-1])
