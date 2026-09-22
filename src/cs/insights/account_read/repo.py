from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import date
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id
from cs.insights.account_read.page import PAGE_VERSION, build_page
from cs.insights.account_read.reliability import FLAG as TAGGER_FLAG
from cs.insights.next_plan import policy


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
SAME_IDEA = 0.6  # share of files two attributes must have in common to count as one idea
OVERFETCH = 3  # rows to rank per kind before de-duplication, as a multiple of the limit
# Hook rate is settled in the first three seconds, so a per-file hook-rate row may only rest
# on something a viewer can see or hear by then: the opening, and the look and voice that run
# from the first frame. "Videos that end on a hard cut have a higher hook rate" is a real
# account-wide difference, but told to one ad as a fix for its hook it cannot work. Whole-ad
# averages (length, cuts per 10s) and whole-ad ideas (concepts, angles, claims) are left out
# on purpose: none is known to be on screen by 0:03. Other metrics are settled after the whole
# ad, so every trait stays eligible there.
HOOK_VISIBLE_DIMS = (
    "opening_shows",
    "opening_shot",
    "opening_scene",
    "first_product_shown",
    "product_reveal",
    "hook_devices",
    "aspect_ratio",
    "captions",
    "production",
    "setting",
    "messenger",
    "messenger_age_band",
    "messenger_gender_presentation",
    "cast",
    "brand_presence",
    "native_format_cues",
    "narration",
    "spoken_language",
    "subtitle_language",
)
_FILE_PATTERNS: dict[tuple[UUID, str, int], list[dict]] = {}
_FILE_PATTERNS_MAX = 2000


def _drop_near_duplicates(rows: list, limit: int) -> list[dict]:
    """Group rows by the idea they are about, then keep the best-ranked row of each idea per
    kind and metric. Rows arrive in rank order, so the survivor is the stronger evidence.

    Two rows are one idea when their cells overlap by `SAME_IDEA` (Jaccard). Each kept row
    carries its `idea` id, because the right amount of collapsing differs by surface: a
    funnel table wants the same attribute on every metric, while a ranked list of tests to
    run wants one line per idea however many metrics agree. Ids are per call, never stored.
    """

    def same(a: set[str], b: set[str]) -> bool:
        return bool(a | b) and len(a & b) / len(a | b) >= SAME_IDEA

    kept: list[dict] = []
    ideas: list[set[str]] = []
    per_bucket: set[tuple[bool, str, int]] = set()
    for r in rows:
        cell = set(r["cell_key"])
        idea = next((i for i, o in enumerate(ideas) if same(cell, o)), None)
        if idea is None:
            ideas.append(cell)
            idea = len(ideas) - 1
        bucket = (r["has"], r["metric"], idea)
        if bucket in per_bucket:
            continue
        if sum(1 for k in kept if k["has"] == r["has"]) >= limit:
            continue
        per_bucket.add(bucket)
        kept.append({k: v for k, v in dict(r).items() if k != "cell_key"} | {"idea": idea})
    return kept


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

    Rows on a trait the tagger flips on are dropped. "This ad has it" and "this ad lacks it"
    are claims about one file's label, and that label is the thing in doubt.

    Near-duplicates are dropped too. Two attributes can pick out almost the same videos --
    "the first three seconds show the product" and "the product first appears under 5s" ran
    as separate rows a place apart -- and a reader takes one idea said twice for two. The
    lower-ranked row goes when it shares `SAME_IDEA` or more of its files with a row already
    kept on the same metric, and every row carries the `idea` id it was grouped into so a
    caller can collapse further. The run's own entanglement pass cannot stand in here: it
    skips flagged tests, and one of those two carried a budget-concentration flag.

    These say "files in this account that do X differ on metric M". They never measure the
    attribute within the file, so a caller must not present one as the cause of this file's
    numbers (the same ruling that caps the ask box's nearest and recipe answers at
    directional). Main effects only: stratified rows answer a narrower question and the
    interaction family is graded separately.

    Hook-rate rows are kept only on traits visible by 0:03 (`HOOK_VISIBLE_DIMS`).

    The filter runs inside Postgres on purpose. `result` is many megabytes per run; shipping
    it to the API on every creative page is what pinned the database on 2026-09-18."""
    run = await db.fetchrow(
        "SELECT id, window_start, window_end FROM account_read_run WHERE org_id=$1 "
        "AND ad_account_id=$2 AND format='video' AND status='done' "
        "ORDER BY created_at DESC LIMIT 1",
        org_id,
        ad_account_id,
    )
    if run is None:
        return []
    run_id = run["id"]
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
              AND NOT (t->'confounds' ? $5)
              AND (t->>'metric' <> 'hook_rate' OR t->>'dimension' = ANY($6))
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
               t->'bin_edges' AS bin_edges, t->'confounds' AS confounds,
               -- Eight hex characters are plenty to tell a few dozen files apart, and keep
               -- this payload in kilobytes. Never widen it to the full hashes.
               ARRAY(SELECT left(s, 8) FROM jsonb_array_elements_text(t->'cell_shas') s) AS cell_key
        FROM ranked WHERE rn <= $4
        ORDER BY has DESC, rn
        """,
        run_id,
        list(FILE_PATTERN_GRADES),
        sha256,
        limit * OVERFETCH,
        TAGGER_FLAG,
        list(HOOK_VISIBLE_DIMS),
    )
    if len(_FILE_PATTERNS) >= _FILE_PATTERNS_MAX:
        _FILE_PATTERNS.clear()
    kept = _drop_near_duplicates(rows, limit)
    # Every row stays as evidence; `prescribable` says whether a page may turn it into an
    # instruction, under the /next plan's policy: never a demographic, and never a language
    # the ad merely lacks.
    for r in kept:
        d = r["dimension"]
        r["prescribable"] = policy.prescribable(d) if r["has"] else policy.explorable(d)
        # The run's window, so the page can say which days the patterns were measured over.
        r["window_start"] = run["window_start"].isoformat() if run["window_start"] else None
        r["window_end"] = run["window_end"].isoformat() if run["window_end"] else None
    _FILE_PATTERNS[key] = kept
    return _FILE_PATTERNS[key]


# What the read could see, against what the account ran. A read covers only tagged files, and
# a page that does not say so reads as if it had seen everything.
_COVERAGE = """
SELECT count(*) AS files,
       count(*) FILTER (WHERE tagged) AS tagged_files,
       coalesce(sum(spend), 0) AS spend,
       coalesce(sum(spend) FILTER (WHERE tagged), 0) AS tagged_spend
FROM (
  SELECT f.id,
         EXISTS (SELECT 1 FROM footage_attribute a WHERE a.footage_id = f.id) AS tagged,
         (SELECT w.spend FROM footage_metric_window w
           WHERE w.footage_id = f.id AND w.window_kind = 'lifetime') AS spend
  FROM footage f
  WHERE f.org_id = $1 AND f.ad_account_id = $2 AND f.format = $3
) x
"""


async def coverage(
    db: Database, org_id: UUID, ad_account_id: UUID, fmt: str = "video", covered_spend=None
) -> dict | None:
    """What the account ran, against what saw it.

    `covered_spend` is the spend of the files a particular read used, and it is what the
    share is measured from. Without it the share describes everything tagged right now --
    right for a tag filter, wrong under a stored read, whose file count belongs to the day it
    ran. Mixing the two once put "99% of account spend" beside a read that covered 87%.
    """
    r = await db.fetchrow(_COVERAGE, org_id, ad_account_id, fmt)
    if r is None or not r["files"]:
        return None
    spend = float(r["spend"])
    covered = float(covered_spend) if covered_spend is not None else float(r["tagged_spend"])
    return {
        "files": r["files"],
        "tagged_files": r["tagged_files"],
        "spend_share": (covered / spend) if spend else None,
    }
