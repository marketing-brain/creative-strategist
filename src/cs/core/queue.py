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
    body = dict(payload or {})
    body.setdefault("schema_version", 1)  # spec section 4: every payload is versioned
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
        body,
        run_after,
        max_attempts,
    )
    return row["id"] if row else None


async def is_dead(db: Database, org_id: UUID, job_type: str, dedup_key: str) -> bool:
    """True once a job with this identity already exhausted its retries. dedup_key alone
    only blocks re-enqueue while a job is pending/running (see job_pending_dedup_idx) --
    a caller that re-runs periodically (e.g. a nightly sync) needs this to stop retrying
    something that has already been given up on, rather than re-enqueueing it forever."""
    return bool(
        await db.fetchval(
            "SELECT 1 FROM job WHERE org_id=$1 AND job_type=$2 AND dedup_key=$3 AND status='dead'"
            " LIMIT 1",
            org_id,
            job_type,
            dedup_key,
        )
    )


# Work that is never urgent: a backlog of it may be thousands of jobs long, and one worker
# claims jobs in order, so without this a drain would put every sync, download and page
# rebuild behind hours of it. False sorts before true, so these are claimed only once
# nothing else is waiting.
BACKGROUND_JOB_TYPES = ("tagging.tag_creative",)


async def claim(db: Database, worker_id: str, job_types: list[str] | None = None) -> Job | None:
    row = await db.fetchrow(
        """
        WITH next AS (
          SELECT id FROM job
          WHERE status = 'pending' AND run_after <= now()
            AND ($1::text[] IS NULL OR job_type = ANY($1))
          ORDER BY (job_type = ANY($3::text[])), run_after, id
          FOR UPDATE SKIP LOCKED
          LIMIT 1
        )
        UPDATE job SET status='running', locked_by=$2, locked_at=now(), attempts = attempts + 1
        WHERE id = (SELECT id FROM next)
        RETURNING id, org_id, job_type, payload, attempts, max_attempts
        """,
        job_types,
        worker_id,
        list(BACKGROUND_JOB_TYPES),
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


async def complete(db: Database, job_id: UUID, worker_id: str) -> None:
    """Mark a job done. Only the worker that holds the lease may do so."""
    await db.execute(
        "UPDATE job SET status='done', finished_at=now(), locked_by=NULL"
        " WHERE id=$1 AND status='running' AND locked_by=$2",
        job_id,
        worker_id,
    )


async def fail(db: Database, job_id: UUID, worker_id: str, error: str) -> None:
    """Record a handler failure, retrying with backoff until max_attempts.

    Only the worker that holds the lease may fail the job. The persisted
    statuses are 'pending', 'running', 'done' and 'dead' -- 'failed' is a
    transient notion only, a failed attempt goes straight back to 'pending'
    (or to 'dead' once attempts reach max_attempts). ``error`` is truncated to
    4000 characters before it is stored.
    """
    await db.execute(
        """
        UPDATE job SET
          error = $3,
          locked_by = NULL,
          status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
          finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
          run_after = now() + make_interval(secs => LEAST(power(2, attempts), 300))
        WHERE id = $1 AND status = 'running' AND locked_by = $2
        """,
        job_id,
        worker_id,
        error[:4000],
    )


async def heartbeat(db: Database, job_id: UUID, worker_id: str) -> bool:
    """Extend the lease on a running job. False means the lease is gone."""
    result = await db.execute(
        "UPDATE job SET locked_at = now() WHERE id=$1 AND status='running' AND locked_by=$2",
        job_id,
        worker_id,
    )
    return int(result.split()[-1]) == 1


async def purge_done(db: Database, older_than_days: int) -> int:
    """Delete finished jobs past their retention. Only 'done' rows go: 'dead' rows are the
    record `is_dead` reads to stop a sweep re-enqueueing something that already gave up, and
    pending/running rows are live work."""
    return int(
        (
            await db.execute(
                "DELETE FROM job WHERE status='done'"
                " AND finished_at < now() - make_interval(days => $1)",
                older_than_days,
            )
        ).split()[-1]
    )


async def reclaim_stale(db: Database, lease_seconds: int) -> int:
    """Release jobs whose lease expired, dead-lettering the ones out of attempts."""
    result = await db.execute(
        """
        UPDATE job SET
          locked_by = NULL,
          locked_at = NULL,
          status = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
          error = CASE WHEN attempts >= max_attempts THEN 'lease expired' ELSE error END,
          finished_at = CASE WHEN attempts >= max_attempts THEN now() ELSE finished_at END
        WHERE status='running' AND locked_at < now() - make_interval(secs => $1)
        """,
        lease_seconds,
    )
    return int(result.split()[-1])
