"""What the settings page needs to say about the Meta sync, in one read.

Two hops sit between Meta and the app: Fivetran copies Meta into the org's raw schema on its
own schedule, then `meta.sync_from_fivetran` folds that schema into the app's tables. The
page shows both sides so a gap reads as what it is: data waiting in the raw schema means
our sync has not run; a raw schema that stopped moving means Fivetran has.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from cs.core.db import Database
from cs.ingest.fivetran.schema import AD_HISTORY, BASIC_AD

SYNC_JOBS = ("meta.sync_from_fivetran", "meta.await_first_sync")
SCHEDULE = "ingest.nightly_sync"
SCHEDULE_EVERY = timedelta(days=1)
# The work a sync queues behind itself, in the order it happens.
STAGES = {
    "files": (
        "meta.download_creative",
        "meta.download_carousel",
        "meta.resolve_instagram_video",
        "meta.resolve_facebook_video",
    ),
    "tagging": ("tagging.tag_creative",),
    "insights": ("insights.creative_insight",),
}


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def _q(schema: str, table: str) -> str:
    # schema is schema_for(org_id)'s output ([a-z0-9_]+), quoted because it can start with a digit.
    return f'"{schema}".{table}'


async def _table_exists(db: Database, schema: str, table: str) -> bool:
    return bool(await db.fetchval("SELECT to_regclass($1) IS NOT NULL", _q(schema, table)))


async def _source(db: Database, schema: str, account_ids: list[str]) -> dict | None:
    """The raw schema, limited to activated accounts so its counts compare with the app's."""
    if not account_ids or not await _table_exists(db, schema, AD_HISTORY):
        return None
    ads = await db.fetchrow(
        f"SELECT count(DISTINCT id) n, max(_fivetran_synced) at FROM {_q(schema, AD_HISTORY)}"
        " WHERE account_id::text = ANY($1)",
        account_ids,
    )
    metrics_through, metrics_at = None, None
    has_reports = await _table_exists(db, schema, BASIC_AD)
    if has_reports:
        m = await db.fetchrow(
            f"SELECT max(date) d, max(_fivetran_synced) at FROM {_q(schema, BASIC_AD)}"
            " WHERE account_id::text = ANY($1)",
            account_ids,
        )
        metrics_through, metrics_at = m["d"], m["at"]
    delivered = max((t for t in (ads["at"], metrics_at) if t), default=None)
    return {
        "ads": ads["n"],
        "metrics_through": metrics_through.isoformat() if metrics_through else None,
        "has_reports": has_reports,
        "delivered_at": _iso(delivered),
    }


async def _app(db: Database, org_id: UUID) -> dict:
    row = await db.fetchrow(
        """
        SELECT (SELECT count(*) FROM ad d JOIN ad_account a ON a.id = d.ad_account_id
                WHERE d.org_id = $1 AND a.status = 'active') ads,
               (SELECT max(day) FROM metric_daily WHERE org_id = $1) metrics_through
        """,
        org_id,
    )
    return {
        "ads": row["ads"],
        "metrics_through": row["metrics_through"].isoformat() if row["metrics_through"] else None,
    }


async def _last_sync(db: Database, org_id: UUID) -> dict | None:
    row = await db.fetchrow(
        """
        SELECT id, job_type, status, attempts, max_attempts, error, created_at, locked_at,
               finished_at
        FROM job WHERE org_id = $1 AND job_type = ANY($2)
        ORDER BY created_at DESC LIMIT 1
        """,
        org_id,
        list(SYNC_JOBS),
    )
    if row is None:
        return None
    # A retry parks the job back at 'pending' with its error kept; the page calls that retrying.
    return {
        "id": str(row["id"]),
        "kind": "first_sync" if row["job_type"] == "meta.await_first_sync" else "sync",
        "status": row["status"],
        "retrying": row["status"] == "pending" and row["attempts"] > 0,
        "error": (row["error"] or "").strip().split("\n")[0][:300] or None,
        "queued_at": _iso(row["created_at"]),
        "started_at": _iso(row["locked_at"]),
        "finished_at": _iso(row["finished_at"]),
    }


async def _auto_sync(db: Database) -> dict:
    last = await db.fetchval("SELECT last_run_at FROM schedule_state WHERE name = $1", SCHEDULE)
    now = datetime.now(UTC)
    if last is not None and last > now:
        return {"enabled": False, "next_run_at": None}
    return {"enabled": True, "next_run_at": _iso(last + SCHEDULE_EVERY if last else now)}


# A job parked far in the future (run_after set to 2100 by hand to stop paid work) is on
# hold, not in progress. Retry backoff never pushes run_after more than minutes out.
HELD_AFTER = "now() + interval '1 day'"


async def _backlog(db: Database, org_id: UUID) -> tuple[dict, int]:
    """Work a sync queued that will still run, by stage, and the count of held jobs."""
    rows = await db.fetch(
        f"""
        SELECT job_type,
               count(*) FILTER (WHERE run_after <= {HELD_AFTER}) n,
               count(*) FILTER (WHERE run_after > {HELD_AFTER}) held
        FROM job
        WHERE org_id = $1 AND status IN ('pending', 'running') AND job_type = ANY($2)
        GROUP BY 1
        """,
        org_id,
        [t for types in STAGES.values() for t in types],
    )
    by_type = {r["job_type"]: r["n"] for r in rows}
    backlog = {stage: sum(by_type.get(t, 0) for t in types) for stage, types in STAGES.items()}
    return backlog, sum(r["held"] for r in rows)


async def sync_status(db: Database, org_id: UUID) -> dict:
    conn = await db.fetchrow("SELECT * FROM meta_connection WHERE org_id = $1", org_id)
    if conn is None:
        return {"connection": None}
    account_ids = [
        r["platform_account_id"]
        for r in await db.fetch(
            "SELECT platform_account_id FROM ad_account WHERE org_id = $1 AND status = 'active'",
            org_id,
        )
    ]
    backlog, held = await _backlog(db, org_id)
    return {
        "connection": {
            "status": conn["sync_status"],
            "error": conn["last_error"],
            "last_synced_at": _iso(conn["last_synced_at"]),
        },
        "source": await _source(db, conn["schema_name"], account_ids),
        "app": await _app(db, org_id),
        "last_sync": await _last_sync(db, org_id),
        "auto_sync": await _auto_sync(db),
        "backlog": backlog,
        "held": held,
    }
