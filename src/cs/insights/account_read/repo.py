from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import date
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.account_read.page import PAGE_VERSION, build_page


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
        "n_units, status, result, read, error, input_tokens, output_tokens, page)"
        " VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)",
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
        build_page(result, read),
    )
    return rid


async def latest_run(db: Database, org_id: UUID, ad_account_id: UUID) -> dict | None:
    """The newest done run when one exists, else the newest run of any status. When a newer
    run failed its checks, its error rides along as `newer_failed_error` so the page can say
    the read is from an earlier run."""
    # `result` is a multi-megabyte JSONB per row. Pulling it for twenty candidates on every
    # page load ran for minutes and pinned the database instance (2026-09-18), and pulling
    # even the one picked row did it again under concurrent loads (2026-09-20). So: list the
    # small columns, then read the picked run's `page`, the stored slice the page draws.
    # Callers that need every test ask for it by run id (`run_result`).
    rows = await db.fetch(
        "SELECT id, status, n_units, error, created_at "
        "FROM account_read_run WHERE org_id=$1 AND ad_account_id=$2 AND format='video' "
        "ORDER BY created_at DESC LIMIT 20",
        org_id,
        ad_account_id,
    )
    if not rows:
        return None
    newest = rows[0]
    done = next((r for r in rows if r["status"] == "done"), None)
    picked = done or newest
    full = await db.fetchrow(
        "SELECT id, status, n_units, window_start, window_end, page, read, error, "
        "created_at FROM account_read_run WHERE id=$1",
        picked["id"],
    )
    if full is None:  # purged between the two queries
        return None
    if full["page"] is None or full["page"].get("v") != PAGE_VERSION:
        full = {**dict(full), "page": await _fill_page(db, full["id"], full["read"])}
    chosen: dict = dict(full)
    chosen["newer_failed_error"] = (
        newest["error"]
        if chosen["id"] != newest["id"] and newest["status"] in ("rejected", "failed")
        else None
    )
    return chosen


# One lock per run: ten page loads arriving together must cost one heavy read, not ten.
_RUN_LOCKS: dict[UUID, asyncio.Lock] = {}
# Whole `result` payloads for the ask box, which maps a question onto every test. A run is
# written once and never edited, so an entry cannot go stale. Each is tens of megabytes once
# parsed, so only the last few runs are kept.
_RESULTS: OrderedDict[UUID, dict] = OrderedDict()
_RESULTS_MAX = 3


def _lock(run_id: UUID) -> asyncio.Lock:
    return _RUN_LOCKS.setdefault(run_id, asyncio.Lock())


async def run_result(db: Database, run_id: UUID) -> dict:
    """A run's full `result`, read from the database at most once per process."""
    async with _lock(run_id):
        if run_id in _RESULTS:
            _RESULTS.move_to_end(run_id)
            return _RESULTS[run_id]
        result = await db.fetchval("SELECT result FROM account_read_run WHERE id=$1", run_id)
        _RESULTS[run_id] = result or {}
        while len(_RESULTS) > _RESULTS_MAX:
            old, _ = _RESULTS.popitem(last=False)
            _RUN_LOCKS.pop(old, None)
        return _RESULTS[run_id]


_FILLED: dict[UUID, dict] = {}
_FILL_LOCKS: dict[UUID, asyncio.Lock] = {}


async def _fill_page(db: Database, run_id: UUID, read: dict | None) -> dict:
    """Build and store `page` for a run written before the column existed (or under an older
    PAGE_VERSION). Loads that arrive together wait on one build: the heavy read and the write
    happen once per run, not once per load."""
    async with _FILL_LOCKS.setdefault(run_id, asyncio.Lock()):
        if run_id not in _FILLED:
            built = build_page(await run_result(db, run_id), read)
            await db.execute("UPDATE account_read_run SET page=$2 WHERE id=$1", run_id, built)
            _FILLED[run_id] = built
        return _FILLED[run_id]


async def poster_paths(db: Database, org_id: UUID, creative_ids: list[UUID]) -> dict[str, str]:
    """Poster still path per creative id, for the creatives that have one."""
    if not creative_ids:
        return {}
    rows = await db.fetch(
        "SELECT DISTINCT ON (t.creative_id) t.creative_id, t.path FROM asset t "
        "JOIN creative c ON c.id = t.creative_id "
        "WHERE c.org_id=$1 AND t.creative_id = ANY($2::uuid[]) AND t.kind='thumbnail' "
        "ORDER BY t.creative_id, t.seq",
        org_id,
        creative_ids,
    )
    return {str(r["creative_id"]): r["path"] for r in rows}


async def purge_runs(db: Database, days: int = 30) -> int:
    res = await db.execute(
        "DELETE FROM account_read_run WHERE created_at < now() - make_interval(days => $1)", days
    )
    return int(res.split()[-1])


# Grades worth showing beside a single creative. Directional and thin tests are noise there.
FILE_PATTERN_GRADES = ("strong", "suggestive")
_FILE_PATTERNS: dict[tuple[UUID, str, int], list[dict]] = {}
_FILE_PATTERNS_MAX = 2000


async def patterns_for_file(
    db: Database, org_id: UUID, ad_account_id: UUID, sha256: str, limit: int = 12
) -> list[dict]:
    """Graded account-wide main effects as they relate to one file, from the newest done run.
    Each row carries `has`: true when the file is in the pattern's cell, false when the file
    is in the run but outside the cell. Strong first, then by size of effect, `limit` rows of
    each kind.

    Rows the file lacks are kept only when the pattern points the good way (higher rate,
    lower cost): "videos that do X perform better and this one does not" is a test to run.
    A harmful trait the file lacks is nothing to act on.

    These say "files in this account that do X differ on metric M". They never measure the
    attribute within the file, so a caller must not present one as the cause of this file's
    numbers (the same ruling that caps the ask box's nearest and recipe answers at
    directional). Main effects only: stratified rows answer a narrower question and the
    interaction family is graded separately.

    The filter runs inside Postgres on purpose. `result` is many megabytes per run; shipping
    it to the API on every creative page is what pinned the database on 2026-09-18."""
    run_id = await db.fetchval(
        "SELECT id FROM account_read_run WHERE org_id=$1 AND ad_account_id=$2 "
        "AND format='video' AND status='done' ORDER BY created_at DESC LIMIT 1",
        org_id,
        ad_account_id,
    )
    if run_id is None:
        return []
    # A run is written once and never edited, so its answer for a file cannot change. The
    # scan below costs about 400 ms of database time; repeat views of a page cost none.
    key = (run_id, sha256, limit)
    if key in _FILE_PATTERNS:
        return _FILE_PATTERNS[key]
    rows = await db.fetch(
        """
        WITH run AS MATERIALIZED (
            SELECT result->'tests' AS tests,
                   EXISTS (SELECT 1 FROM jsonb_array_elements(result->'units') u
                           WHERE u->>'sha256' = $3) AS is_unit
            FROM account_read_run WHERE id = $1
        ),
        t AS (
            SELECT t, (t->'cell_shas' ? $3) AS has, (t->>'change')::float8 AS change
            FROM run, jsonb_array_elements(run.tests) t
            WHERE run.is_unit AND t->>'group' IS NULL AND t->>'grade' = ANY($2)
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY has ORDER BY (t->>'grade' = 'strong') DESC, abs(change) DESC
            ) AS rn
            FROM t
            WHERE has OR CASE WHEN t->>'metric' = 'cpa' THEN change < 0 ELSE change > 0 END
        )
        SELECT has, t->>'dimension' AS dimension, t->>'value' AS value, t->>'metric' AS metric,
               t->>'grade' AS grade, change,
               (t->>'n_cell')::int AS n_cell, (t->>'n_complement')::int AS n_complement,
               (t->>'cell_value')::float8 AS cell_value, (t->>'comp_value')::float8 AS comp_value,
               t->'bin_edges' AS bin_edges, t->'confounds' AS confounds
        FROM ranked WHERE rn <= $4
        ORDER BY has DESC, rn
        """,
        run_id,
        list(FILE_PATTERN_GRADES),
        sha256,
        limit,
    )
    if len(_FILE_PATTERNS) >= _FILE_PATTERNS_MAX:
        _FILE_PATTERNS.clear()
    _FILE_PATTERNS[key] = [dict(r) for r in rows]
    return _FILE_PATTERNS[key]
