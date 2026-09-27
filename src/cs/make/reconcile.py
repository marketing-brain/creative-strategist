"""Mark work as failed when the worker has already given up on its job.

A brief, render or generated opening is written as 'queued' and its job enqueued. If the worker
cannot run the job (no handler for it, a crash before the handler updates the row), the job dies
but the row stayed 'queued', and the page showed "Writing the brief" forever (2026-09-26: the
EC2 worker had no briefs.generate handler). On read, any row still queued or running whose job
is dead is marked failed with the job's own error.
"""

from __future__ import annotations

from uuid import UUID

from cs.core.db import Database

# (table, job type, dedup key prefix) for each kind of work a person starts from Make or What
# works. A row can have more than one job (a script video's clips run as a second job), so the
# key matches by prefix.
WORK = (
    ("creative_brief", "briefs.generate", "brief:", "updated_at = now()"),
    ("brief_render", "briefs.render", "render:", "finished_at = coalesce(t.finished_at, now())"),
    ("generation", "generate.opening_edit", "gen:", "finished_at = coalesce(t.finished_at, now())"),
    ("script_video", "make.script_video", "script_video:", "updated_at = now()"),
)


async def reconcile(db: Database, org_id: UUID) -> int:
    fixed = 0
    for table, job_type, prefix, stamp in WORK:
        rows = await db.fetch(
            f"""
            UPDATE {table} t SET status = 'failed',
                   error = CASE WHEN j.error LIKE 'no handler registered%'
                                THEN 'The server is not set up to run this yet. It works once the new version is deployed.'
                                ELSE 'The server could not run this: ' || coalesce(j.error, 'unknown error') END,
                   {stamp}
            FROM job j
            WHERE t.org_id = $1 AND t.status IN ('queued', 'running')
              AND j.org_id = t.org_id AND j.job_type = $2 AND j.dedup_key LIKE $3 || t.id::text || '%' AND j.status = 'dead'
            RETURNING t.id
            """,
            org_id,
            job_type,
            prefix,
        )
        fixed += len(rows)
    return fixed
