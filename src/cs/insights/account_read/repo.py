from __future__ import annotations

from datetime import date
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


async def insert_run(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID,
    *,
    status: str,
    n_units: int,
    result: dict,
    read: dict | None,
    error: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> UUID:
    rid = new_id()
    summary = result.get("summary") or {}

    def as_date(v):
        return date.fromisoformat(v) if isinstance(v, str) else v

    await db.execute(
        "INSERT INTO account_read_run (id, org_id, ad_account_id, window_start, window_end, "
        "n_units, status, result, read, error, input_tokens, output_tokens)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
        rid,
        org_id,
        ad_account_id,
        as_date(summary.get("first_day")),
        as_date(summary.get("last_day")),
        n_units,
        status,
        result,  # jsonb codec in cs.core.db encodes dicts
        read,
        error,
        input_tokens,
        output_tokens,
    )
    return rid


async def latest_run(db: Database, org_id: UUID, ad_account_id: UUID) -> dict | None:
    row = await db.fetchrow(
        "SELECT id, status, n_units, window_start, window_end, result, read, error, created_at "
        "FROM account_read_run WHERE org_id=$1 AND ad_account_id=$2 AND format='video' "
        "ORDER BY created_at DESC LIMIT 1",
        org_id,
        ad_account_id,
    )
    return dict(row) if row is not None else None  # jsonb columns arrive decoded


async def purge_runs(db: Database, days: int = 30) -> int:
    res = await db.execute(
        "DELETE FROM account_read_run WHERE created_at < now() - make_interval(days => $1)", days
    )
    return int(res.split()[-1])
