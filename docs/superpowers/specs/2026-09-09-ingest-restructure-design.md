# Restructure src/cs/ingest/

**Date:** 2026-09-09
**Status:** Approved for planning
**Scope:** Pure code reorganization of `src/cs/ingest/` and the data-access layer it currently
hosts. No behavior, data, SQL, or table/column change of any kind — every function keeps its
exact signature and body (two exceptions noted below are refactors of *how* the same queries
run, not what they return).

## 1. Motivation

`src/cs/ingest/` grew during the Fivetran migration (`docs/superpowers/specs/2026-09-06-fivetran-meta-migration-design.md`)
into two things that don't belong in one package:

1. **`repo.py` (461 lines)** is not ingest-internal. It's imported by `api/routers/{accounts,connections,creatives,tags}.py`,
   `tagging/{runner,jobs}.py`, and `insights/copilot.py` -- seven consumers outside `ingest/`.
   It's the app's de facto shared data-access layer, mislabeled by location.
2. **The Fivetran-specific transform/insights/schema/fixtures files** sit flat alongside
   job orchestration (`jobs.py`) and asset mirroring (`service.py`), with no grouping that
   signals they're one pipeline stage.

Two small pieces of duplicated logic compound the confusion: a `row_number() OVER (...)
WHERE rn=1` "latest row per id" block copy-pasted three times in `fivetran_transform.py`,
and six hand-written `_action_totals()` calls/variables in `fivetran_insights.py` for the six
insight breakdown tables.

This is scoped as pure restructuring. Trimming which Fivetran tables/fields are actually
synced (the placement breakdown, `video_p50`/`video_p100`, moving to `basic_ad` prebuilt
reports) was explicitly discussed and deferred to a separate future task -- this spec does
not touch what data flows through the pipeline, only where the code that moves it lives.

## 2. Decisions taken

| Decision | Choice |
|---|---|
| `repo.py` location | Moves out of `ingest/` to `src/cs/core/repo/` -- `core/` already holds cross-cutting infra (`config`, `db`, `queue`, `blobs`, `metrics.py`) that every domain package depends on; this fits that existing pattern |
| `repo.py` split | By entity into six files: `accounts.py`, `connections.py`, `creatives.py`, `ads.py`, `assets.py`, `metrics.py` |
| Call-site API | Unchanged. `cs/core/repo/__init__.py` re-exports every function explicitly, so every call site keeps `repo.upsert_ad_account(...)`, `repo.list_creatives(...)`, etc. -- only the *import line* changes, never a call site |
| `partitions.py` | Moves to `cs/core/repo/partitions.py` (correction from the chat design, caught while writing this spec) -- `ensure_metric_partition` is used only by `upsert_metrics`, which is moving to `core/repo/metrics.py`; leaving it in `ingest/` would make `core` depend on `ingest`, inverting the intended layering |
| Fivetran files | Grouped into a new `src/cs/ingest/fivetran/` subpackage: `schema.py`, `transform.py`, `insights.py`, `fixtures.py` |
| `service.py` | Renamed to `src/cs/ingest/assets.py` -- "service" named nothing; the module downloads/mirrors creative assets from Meta's CDN |
| `jobs.py` | Stays at `src/cs/ingest/jobs.py`, imports updated |
| Dedup extraction #1 | `fivetran/transform.py` gains a `_latest_rows(db, schema, table)` helper replacing the 3x copy-pasted dedup SQL |
| Dedup extraction #2 | `fivetran/insights.py` replaces the 6 hand-written `_action_totals()` variables with a loop over a `{output_key: table_name}` mapping -- still 6 queries, same results |
| Behavior/data | Byte-for-byte unchanged. Same tables, same columns, same SQL (except the two extractions above, which are structurally identical queries, not new ones), same values written to every table |

## 3. New layout

```
src/cs/core/repo/
  __init__.py       # re-exports every function from the six submodules below
  accounts.py        # upsert_ad_account, list_ad_accounts, list_active_ad_account_ids,
                      # get_ad_account, set_ad_account_status
  connections.py      # upsert_meta_connection, get_meta_connection, get_meta_connection_by_id,
                      # set_meta_connection_status, list_ready_meta_connections_with_active_account
  creatives.py        # upsert_creative, list_creatives, count_creatives, get_creative,
                      # creative_metrics, _tag_filter_sql (private)
  ads.py               # upsert_ad
  assets.py            # insert_asset, set_primary_asset, delete_assets_by_kind,
                      # creative_has_asset, creative_has_primary_asset, get_asset_by_seq,
                      # list_assets_by_kind
  metrics.py           # upsert_metrics, METRIC_CHUNK_SIZE, _UPSERT_METRIC_SQL (private)
  partitions.py        # ensure_metric_partition, _month_bounds (private) -- moved from ingest/

src/cs/ingest/
  __init__.py
  jobs.py              # unchanged content, imports updated
  assets.py             # renamed from service.py, unchanged content
  fivetran/
    __init__.py
    schema.py           # was fivetran_schema.py, unchanged content
    transform.py         # was fivetran_transform.py, + _latest_rows() helper
    insights.py           # was fivetran_insights.py, + breakdown-table loop
    fixtures.py           # was fivetran_fixtures.py, unchanged content (updates its own
                          # import of schema.py to the new path; does not import
                          # transform.py/insights.py despite its docstring naming both)
```

Note `partitions.py`'s move means `tests/ingest/test_partitions.py` moves too, to
`tests/core/test_repo_partitions.py` (see Testing below), since it now tests a `core/repo`
module, not an `ingest` one.

## 4. `__init__.py` re-export shape

```python
# src/cs/core/repo/__init__.py
from cs.core.repo.accounts import (
    get_ad_account,
    list_active_ad_account_ids,
    list_ad_accounts,
    set_ad_account_status,
    upsert_ad_account,
)
from cs.core.repo.ads import upsert_ad
from cs.core.repo.assets import (
    creative_has_asset,
    creative_has_primary_asset,
    delete_assets_by_kind,
    get_asset_by_seq,
    insert_asset,
    list_assets_by_kind,
    set_primary_asset,
)
from cs.core.repo.connections import (
    get_meta_connection,
    get_meta_connection_by_id,
    list_ready_meta_connections_with_active_account,
    set_meta_connection_status,
    upsert_meta_connection,
)
from cs.core.repo.creatives import (
    count_creatives,
    creative_metrics,
    get_creative,
    list_creatives,
    upsert_creative,
)
from cs.core.repo.metrics import upsert_metrics
```
Explicit names, no `import *` -- keeps the re-export list itself a readable index of the
module's public surface, consistent with how `fivetran_schema.py` was written earlier this
session.

## 5. Migration mechanics

This is a mechanical move-and-relink, not a logic change (outside the two named
extractions). Every file below needs its import lines updated; no other line changes.

**Source files that move/rename:**
- `src/cs/ingest/repo.py` -> split across `src/cs/core/repo/{accounts,connections,creatives,ads,assets,metrics}.py`
- `src/cs/ingest/partitions.py` -> `src/cs/core/repo/partitions.py`
- `src/cs/ingest/service.py` -> `src/cs/ingest/assets.py`
- `src/cs/ingest/fivetran_schema.py` -> `src/cs/ingest/fivetran/schema.py`
- `src/cs/ingest/fivetran_transform.py` -> `src/cs/ingest/fivetran/transform.py`
- `src/cs/ingest/fivetran_insights.py` -> `src/cs/ingest/fivetran/insights.py`
- `src/cs/ingest/fivetran_fixtures.py` -> `src/cs/ingest/fivetran/fixtures.py`

**Import sites requiring updates (enumerated by grep against the current tree):**

`repo.py` (`from cs.ingest import repo` -> `from cs.core import repo`):
`src/cs/insights/copilot.py`, `src/cs/ingest/assets.py` (ex-service.py), `src/cs/ingest/fivetran/transform.py`,
`src/cs/ingest/fivetran/insights.py`, `src/cs/tagging/jobs.py`, `src/cs/tagging/runner.py`,
`src/cs/api/routers/{tags,accounts,connections,creatives}.py`, plus tests:
`tests/insights/seed.py`, `tests/ingest/test_service.py`, `tests/ingest/test_jobs.py`,
`tests/tagging/{test_reconcile,test_repo,test_runner,test_embed,test_batch}.py`,
`tests/ingest/test_repo.py`, `tests/api/{test_accounts_and_creatives,test_tags}.py`,
`tests/e2e/test_loop.py`, `scripts/seed_dev_fivetran.py`

`fivetran_transform.py` -> `fivetran/transform.py`:
`src/cs/connectors/fivetran/connect.py`, `src/cs/ingest/jobs.py`,
`tests/ingest/test_fivetran_transform.py`, `tests/e2e/test_loop.py`, `scripts/seed_dev_fivetran.py`
(note: `fivetran_fixtures.py`'s docstring *mentions* `fivetran_transform.py`/`fivetran_insights.py`
by name but does not import either -- verified against its actual import lines, not just grep noise)

`fivetran_insights.py` -> `fivetran/insights.py`:
`src/cs/ingest/jobs.py`,
`tests/ingest/test_fivetran_insights.py`, `tests/e2e/test_loop.py`

`fivetran_schema.py` -> `fivetran/schema.py`:
`src/cs/ingest/fivetran/transform.py`, `src/cs/ingest/fivetran/insights.py`, `src/cs/ingest/fivetran/fixtures.py`

`fivetran_fixtures.py` -> `fivetran/fixtures.py`:
`tests/ingest/test_fivetran_insights.py`, `tests/ingest/test_jobs.py`, `tests/ingest/test_fivetran_transform.py`,
`tests/e2e/test_loop.py`, `scripts/seed_dev_fivetran.py`

`service.py` -> `assets.py` (note: distinct from the new `core/repo/assets.py` -- different
package paths, no collision, but call sites that need both should alias one on import, e.g.
`from cs.core import repo` + `from cs.ingest import assets`, which is how `jobs.py` already
needs to use both):
`src/cs/ingest/jobs.py`, `tests/ingest/test_service.py` -> renamed `tests/ingest/test_assets.py`

`partitions.py` -> `core/repo/partitions.py`:
`src/cs/core/repo/metrics.py` (the new home of `upsert_metrics`), `tests/ingest/test_partitions.py`
-> renamed `tests/core/test_repo_partitions.py`

Use `git mv` for each file move so history is preserved, then edit imports.

## 6. The two dedup extractions (detail)

**`fivetran/transform.py`:**
```python
async def _latest_rows(db, schema: str, table: str) -> list[asyncpg.Record]:
    return await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, table)}
        ) t WHERE rn = 1
    """)
```
`_sync_ads_and_creatives` calls this three times (`CREATIVE_HISTORY`, `AD_HISTORY`,
`AD_VIDEO_HISTORY`) instead of inlining the same SQL shape three times. Identical queries,
identical results.

**`fivetran/insights.py`:**
```python
_BREAKDOWN_TABLES = {
    "actions": AD_INSIGHTS_ACTIONS,
    "action_values": AD_INSIGHTS_ACTION_VALUES,
    "video_3s": AD_INSIGHTS_VIDEO_PLAY_ACTIONS,
    "video_thruplay": AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS,
    "video_p50": AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS,
    "video_p100": AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS,
}
breakdowns = {key: await _action_totals(db, schema, table) for key, table in _BREAKDOWN_TABLES.items()}
```
`sync_insights` reads `breakdowns["actions"]`, `breakdowns["video_3s"]`, etc. instead of six
separately named variables. Still six queries against six tables -- same as today.

## 7. Testing

Run the full `pytest` suite, not just `tests/ingest/` -- `repo.py` moving affects
`tests/tagging/`, `tests/api/`, `tests/insights/`, and `tests/e2e/` as enumerated above.
Confirm `ruff check` and `ruff format --check` pass on every touched/moved file. No new
tests are needed since no behavior changes; existing tests moving with their modules
(`test_partitions.py` -> `tests/core/test_repo_partitions.py`, `test_service.py` ->
`tests/ingest/test_assets.py`) is the only test-side change, plus import-path updates
throughout.

## 8. Non-goals

- No change to which Fivetran tables/columns are read (that's the separate, deferred
  trimming discussion from earlier this session).
- No change to `metric_daily`'s schema, `ad_insights` query windowing, or account scoping.
- No change to any function's SQL, parameters, return type, or behavior, beyond the two
  named dedup extractions (which are refactors of *how* the same six/three queries run, not
  what they return).
- No new abstractions beyond the entity split already decided (no repository base class,
  no ORM, no generic CRUD layer).
