# Restructure src/cs/ingest/ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `src/cs/ingest/repo.py` to a proper shared `src/cs/core/repo/` package (split by
entity), group the Fivetran-specific files into an `src/cs/ingest/fivetran/` subpackage, rename
`service.py` to `assets.py`, and extract two duplicated code patterns -- all with zero behavior
or data change.

**Architecture:** Six tasks, each ending in a full green test suite: (1) build the new
`core/repo/` package without touching any caller, (2) cut every caller over and delete the old
`repo.py`/`partitions.py`, (3) rename `service.py` to `assets.py`, (4) build the new
`ingest/fivetran/` package (with the two dedup extractions) without touching any caller,
(5) cut every caller over and delete the old flat `fivetran_*.py` files, (6) final full-suite +
lint verification against the spec's non-goals.

**Tech Stack:** Python, asyncpg, pytest, ruff. macOS/BSD `sed` (`sed -i ''`, not GNU `sed -i`).

**Spec:** `docs/superpowers/specs/2026-09-09-ingest-restructure-design.md`

## Global Constraints

- No behavior, SQL, table/column, or return-value change anywhere, except the two named dedup
  extractions (Task 4), which are refactors of *how* the same queries run, not what they return.
- Every call site keeps calling `repo.xxx(...)` exactly as today -- only import lines change.
- Import ordering/formatting does not need to be manually perfected mid-task: `ruff check --fix`
  + `ruff format` in Task 6 (and after each task, per its own step) normalizes it. Don't hand-sort
  imports.
- Use `git mv` when a task moves/renames a whole file unchanged, so history is preserved. Use
  `Write`/`Edit` for files whose content changes as part of the move.
- Run the *full* `pytest` suite at the end of every task (not just `tests/ingest/`), since
  `repo.py` is consumed by `tagging/`, `api/`, `insights/`, and `e2e/` tests too.

---

### Task 1: Build `src/cs/core/repo/`

**Files:**
- Create: `src/cs/core/repo/__init__.py`, `src/cs/core/repo/accounts.py`,
  `src/cs/core/repo/connections.py`, `src/cs/core/repo/creatives.py`, `src/cs/core/repo/ads.py`,
  `src/cs/core/repo/assets.py`, `src/cs/core/repo/metrics.py`, `src/cs/core/repo/partitions.py`
- Test: none yet touched -- this task adds new, currently-unused files only. `src/cs/ingest/repo.py`
  and `src/cs/ingest/partitions.py` are untouched and still exclusively used by every caller.

**Interfaces:**
- Produces: `cs.core.repo` package exposing (via `__init__.py` re-export) every function
  `cs.ingest.repo` exposes today: `upsert_ad_account`, `list_ad_accounts`,
  `list_active_ad_account_ids`, `get_ad_account`, `set_ad_account_status`,
  `upsert_meta_connection`, `get_meta_connection`, `get_meta_connection_by_id`,
  `set_meta_connection_status`, `list_ready_meta_connections_with_active_account`,
  `upsert_creative`, `upsert_ad`, `insert_asset`, `set_primary_asset`, `delete_assets_by_kind`,
  `creative_has_asset`, `creative_has_primary_asset`, `get_asset_by_seq`, `list_assets_by_kind`,
  `upsert_metrics`, `list_creatives`, `count_creatives`, `get_creative`, `creative_metrics`,
  plus `ensure_metric_partition` from `cs.core.repo.partitions`.

- [ ] **Step 1: Create `src/cs/core/repo/accounts.py`**

```python
from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_ad_account(
    db: Database,
    org_id: UUID,
    platform_account_id: str,
    name: str,
    currency: str,
    meta_connection_id: UUID | None = None,
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, currency,
                                meta_connection_id, status)
        VALUES ($1, $2, 'meta', $3, $4, $5, $6, 'paused')
        ON CONFLICT (org_id, platform, platform_account_id) DO UPDATE
          SET name = EXCLUDED.name, currency = EXCLUDED.currency,
              meta_connection_id = EXCLUDED.meta_connection_id
        RETURNING id
        """,
        new_id(),
        org_id,
        platform_account_id,
        name,
        currency,
        meta_connection_id,
    )
    return row["id"]


async def list_ad_accounts(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT a.id, a.platform, a.platform_account_id, a.name, a.currency, a.status,
               a.sync_cursor, a.created_at,
               c.sync_status AS connection_status, c.last_error AS connection_error
        FROM ad_account a LEFT JOIN meta_connection c ON c.id = a.meta_connection_id
        WHERE a.org_id = $1 ORDER BY a.created_at
        """,
        org_id,
    )


async def list_active_ad_account_ids(db: Database, org_id: UUID) -> set[UUID]:
    """Accounts the user has explicitly activated -- the consent boundary the Fivetran
    transform gates ingestion on. A discovered-but-paused account gets no ads, creatives,
    assets, or tagging until it appears here."""
    rows = await db.fetch("SELECT id FROM ad_account WHERE org_id=$1 AND status='active'", org_id)
    return {r["id"] for r in rows}


async def get_ad_account(db: Database, org_id: UUID, account_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT * FROM ad_account WHERE org_id = $1 AND id = $2", org_id, account_id
    )


async def set_ad_account_status(db: Database, org_id: UUID, account_id: UUID, status: str) -> None:
    await db.execute(
        "UPDATE ad_account SET status = $3 WHERE org_id = $1 AND id = $2",
        org_id,
        account_id,
        status,
    )
```

- [ ] **Step 2: Create `src/cs/core/repo/connections.py`**

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_meta_connection(
    db: Database, org_id: UUID, fivetran_connector_id: str, schema_name: str
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO meta_connection (id, org_id, fivetran_connector_id, schema_name, sync_status)
        VALUES ($1, $2, $3, $4, 'pending')
        ON CONFLICT (org_id) DO UPDATE
          SET fivetran_connector_id = EXCLUDED.fivetran_connector_id,
              schema_name = EXCLUDED.schema_name, sync_status = 'pending', last_error = NULL
        RETURNING id
        """,
        new_id(),
        org_id,
        fivetran_connector_id,
        schema_name,
    )
    return row["id"]


async def get_meta_connection(db: Database, org_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM meta_connection WHERE org_id = $1", org_id)


async def get_meta_connection_by_id(db: Database, connection_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow("SELECT * FROM meta_connection WHERE id = $1", connection_id)


async def set_meta_connection_status(
    db: Database,
    connection_id: UUID,
    status: str,
    *,
    last_synced_at: datetime | None = None,
    last_error: str | None = None,
) -> None:
    await db.execute(
        "UPDATE meta_connection SET sync_status=$2, last_synced_at=COALESCE($3, last_synced_at), "
        "last_error=$4 WHERE id=$1",
        connection_id,
        status,
        last_synced_at,
        last_error,
    )


async def list_ready_meta_connections_with_active_account(db: Database) -> list[asyncpg.Record]:
    """Connections eligible for a nightly re-sync: healthy, and at least one account someone
    has activated -- mirrors the old nightly_sync's `WHERE status = 'active'` filter, just
    re-expressed at the connection level since discovery now happens per-org, not per-account."""
    return await db.fetch(
        """
        SELECT DISTINCT c.id, c.org_id, c.schema_name FROM meta_connection c
        JOIN ad_account a ON a.meta_connection_id = c.id
        WHERE c.sync_status = 'ready' AND a.status = 'active'
        """
    )
```

- [ ] **Step 3: Create `src/cs/core/repo/ads.py`**

```python
from __future__ import annotations

from uuid import UUID

from cs.connectors.base import PlatformAd
from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_ad(
    db: Database, org_id: UUID, ad_account_id: UUID, a: PlatformAd, creative_id: UUID
) -> UUID:
    row = await db.fetchrow(
        """
        INSERT INTO ad (id, org_id, ad_account_id, platform_ad_id, creative_id, adset_id,
                        campaign_id, name, status)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (ad_account_id, platform_ad_id) DO UPDATE
          SET status = EXCLUDED.status, name = EXCLUDED.name, creative_id = EXCLUDED.creative_id,
              last_seen_at = now()
        RETURNING id
        """,
        new_id(),
        org_id,
        ad_account_id,
        a.platform_ad_id,
        creative_id,
        a.adset_id,
        a.campaign_id,
        a.name,
        a.status,
    )
    return row["id"]
```

- [ ] **Step 4: Create `src/cs/core/repo/assets.py`**

```python
from __future__ import annotations

from uuid import UUID

import asyncpg

from cs.core.db import Database
from cs.core.ids import new_id


async def insert_asset(
    db: Database,
    org_id: UUID,
    creative_id: UUID,
    kind: str,
    path: str,
    sha256: str,
    mime: str,
    width: int | None = None,
    height: int | None = None,
    duration_ms: int | None = None,
    seq: int = 0,
) -> UUID:
    aid = new_id()
    await db.execute(
        "INSERT INTO asset (id, org_id, creative_id, kind, path, sha256, mime, width, height, "
        "duration_ms, seq) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        aid,
        org_id,
        creative_id,
        kind,
        path,
        sha256,
        mime,
        width,
        height,
        duration_ms,
        seq,
    )
    return aid


async def set_primary_asset(db: Database, org_id: UUID, creative_id: UUID, asset_id: UUID) -> None:
    await db.execute(
        "UPDATE creative SET primary_asset_id = $3 WHERE org_id = $1 AND id = $2",
        org_id,
        creative_id,
        asset_id,
    )


async def delete_assets_by_kind(
    db: Database, org_id: UUID, creative_id: UUID, kinds: tuple[str, ...]
) -> None:
    await db.execute(
        "DELETE FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind = ANY($3)",
        org_id,
        creative_id,
        list(kinds),
    )


async def creative_has_asset(db: Database, org_id: UUID, creative_id: UUID, kind: str) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 LIMIT 1",
            org_id,
            creative_id,
            kind,
        )
    )


async def creative_has_primary_asset(db: Database, org_id: UUID, creative_id: UUID) -> bool:
    return bool(
        await db.fetchval(
            "SELECT 1 FROM creative WHERE org_id=$1 AND id=$2 AND primary_asset_id IS NOT NULL",
            org_id,
            creative_id,
        )
    )


async def get_asset_by_seq(
    db: Database, org_id: UUID, creative_id: UUID, kind: str, seq: int
) -> asyncpg.Record | None:
    return await db.fetchrow(
        "SELECT id FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 AND seq=$4",
        org_id,
        creative_id,
        kind,
        seq,
    )


async def list_assets_by_kind(
    db: Database, org_id: UUID, creative_id: UUID, kind: str
) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT * FROM asset WHERE org_id=$1 AND creative_id=$2 AND kind=$3 ORDER BY seq",
        org_id,
        creative_id,
        kind,
    )
```

- [ ] **Step 5: Create `src/cs/core/repo/creatives.py`**

```python
from __future__ import annotations

from datetime import date
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformCreative
from cs.core.db import Database
from cs.core.ids import new_id


async def upsert_creative(
    db: Database, org_id: UUID, ad_account_id: UUID, c: PlatformCreative, raw_path: str
) -> tuple[UUID, bool]:
    row = await db.fetchrow(
        """
        INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id, format, headline,
                              primary_text, description, cta_type, link_url, raw_path)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        ON CONFLICT (ad_account_id, platform_creative_id) DO UPDATE SET last_seen_at = now()
        RETURNING id, (xmax = 0) AS created
        """,
        new_id(),
        org_id,
        ad_account_id,
        c.platform_creative_id,
        c.format,
        c.headline,
        c.primary_text,
        c.description,
        c.cta_type,
        c.link_url,
        raw_path,
    )
    return row["id"], row["created"]


def _tag_filter_sql(dim_param: str, value_param: str) -> str:
    return f"""
      AND ({dim_param}::text IS NULL OR EXISTS (
            SELECT 1 FROM tag t
            WHERE t.tag_run_id = (
                SELECT id FROM tag_run tr
                WHERE tr.creative_id = c.id AND tr.status = 'done'
                ORDER BY tr.finished_at DESC LIMIT 1
            ) AND t.dimension = {dim_param} AND t.value = {value_param}
          ))
"""


async def list_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    limit: int = 50,
    offset: int = 0,
    dimension: str | None = None,
    value: str | None = None,
) -> list[asyncpg.Record]:
    return await db.fetch(
        f"""
        SELECT c.id, c.ad_account_id, c.platform_creative_id, c.format, c.headline, c.primary_text,
               c.cta_type, c.link_url, c.first_seen_at, c.last_seen_at,
               a.path AS primary_asset_path, a.mime AS primary_asset_mime,
               COALESCE(m.spend, 0) AS spend_30d, m.impressions AS impressions_30d,
               m.clicks AS clicks_30d, m.conversions AS conversions_30d,
               m.conversion_value AS conversion_value_30d
        FROM creative c
        LEFT JOIN asset a ON a.id = c.primary_asset_id
        LEFT JOIN (
            SELECT creative_id, sum(spend) AS spend, sum(impressions) AS impressions,
                   sum(clicks) AS clicks, sum(conversions) AS conversions,
                   sum(conversion_value) AS conversion_value
            FROM metric_daily
            WHERE org_id = $1 AND day >= current_date - interval '30 days'
            GROUP BY creative_id
        ) m ON m.creative_id = c.id
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
          {_tag_filter_sql("$6", "$7")}
        ORDER BY c.first_seen_at DESC LIMIT $4 OFFSET $5
        """,
        org_id,
        ad_account_id,
        format,
        limit,
        offset,
        dimension,
        value,
    )


async def count_creatives(
    db: Database,
    org_id: UUID,
    ad_account_id: UUID | None = None,
    format: str | None = None,
    dimension: str | None = None,
    value: str | None = None,
) -> int:
    return await db.fetchval(
        f"""
        SELECT count(*) FROM creative c
        WHERE c.org_id = $1 AND ($2::uuid IS NULL OR c.ad_account_id = $2)
          AND ($3::text IS NULL OR c.format = $3)
          {_tag_filter_sql("$4", "$5")}
        """,
        org_id,
        ad_account_id,
        format,
        dimension,
        value,
    )


async def get_creative(db: Database, org_id: UUID, creative_id: UUID) -> asyncpg.Record | None:
    return await db.fetchrow(
        """
        SELECT c.*, a.path AS primary_asset_path, a.mime AS primary_asset_mime
        FROM creative c LEFT JOIN asset a ON a.id = c.primary_asset_id
        WHERE c.org_id = $1 AND c.id = $2
        """,
        org_id,
        creative_id,
    )


async def creative_metrics(
    db: Database, org_id: UUID, creative_id: UUID, since: date, until: date
) -> list[asyncpg.Record]:
    return await db.fetch(
        """
        SELECT day, sum(spend) AS spend, sum(impressions) AS impressions, sum(clicks) AS clicks,
               sum(link_clicks) AS link_clicks, sum(conversions) AS conversions,
               sum(conversion_value) AS conversion_value, sum(video_3s_views) AS video_3s_views,
               sum(video_thruplay) AS video_thruplay
        FROM metric_daily WHERE org_id=$1 AND creative_id=$2 AND day BETWEEN $3 AND $4
        GROUP BY day ORDER BY day
        """,
        org_id,
        creative_id,
        since,
        until,
    )
```

- [ ] **Step 6: Create `src/cs/core/repo/partitions.py`** (moved verbatim from `src/cs/ingest/partitions.py`)

```python
from datetime import date

import asyncpg

from cs.core.db import Database


def _month_bounds(day: date) -> tuple[date, date]:
    start = day.replace(day=1)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


async def ensure_metric_partition(db: Database, day: date) -> None:
    start, end = _month_bounds(day)
    name = f"metric_daily_{start:%Y%m}"
    try:
        await db.execute(
            f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF metric_daily "
            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
        )
    except (asyncpg.exceptions.DuplicateTableError, asyncpg.exceptions.UniqueViolationError):
        # Concurrent syncs race here on the first day of a month: IF NOT EXISTS does not
        # protect against another transaction creating the same partition in parallel.
        pass
```

- [ ] **Step 7: Create `src/cs/core/repo/metrics.py`**

```python
from __future__ import annotations

from uuid import UUID

from cs.connectors.base import MetricRow
from cs.core.db import Database
from cs.core.repo.partitions import ensure_metric_partition

METRIC_CHUNK_SIZE = 1000

_UPSERT_METRIC_SQL = """
    INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement,
        spend, impressions, clicks, link_clicks, conversions, conversion_value,
        video_3s_views, video_thruplay, video_p50, video_p100, raw)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    ON CONFLICT (creative_id, ad_id, day, placement) DO UPDATE SET
        spend=EXCLUDED.spend, impressions=EXCLUDED.impressions, clicks=EXCLUDED.clicks,
        link_clicks=EXCLUDED.link_clicks, conversions=EXCLUDED.conversions,
        conversion_value=EXCLUDED.conversion_value,
        video_3s_views=EXCLUDED.video_3s_views, video_thruplay=EXCLUDED.video_thruplay,
        video_p50=EXCLUDED.video_p50, video_p100=EXCLUDED.video_p100, raw=EXCLUDED.raw
"""


async def upsert_metrics(
    db: Database, org_id: UUID, ad_account_id: UUID, rows: list[MetricRow]
) -> tuple[int, int]:
    """Upsert metric rows, returning (written, skipped).

    Rows whose ad is unknown to us are skipped: Meta insights can name ads that the ad
    listing did not return (deleted or out of the page window).
    """
    if not rows:
        return 0, 0
    ads = await db.fetch(
        "SELECT id, platform_ad_id, creative_id FROM ad WHERE org_id=$1 AND ad_account_id=$2",
        org_id,
        ad_account_id,
    )
    by_pid = {a["platform_ad_id"]: a for a in ads}
    params: list[tuple] = []
    skipped = 0
    for r in rows:
        a = by_pid.get(r.platform_ad_id)
        if not a:
            skipped += 1
            continue
        params.append(
            (
                org_id,
                ad_account_id,
                a["creative_id"],
                a["id"],
                r.day,
                r.placement,
                r.spend,
                r.impressions,
                r.clicks,
                r.link_clicks,
                r.conversions,
                r.conversion_value,
                r.video_3s_views,
                r.video_thruplay,
                r.video_p50,
                r.video_p100,
                r.raw,
            )
        )
    if not params:
        return 0, skipped
    for month in {t[4].replace(day=1) for t in params}:
        await ensure_metric_partition(db, month)
    for i in range(0, len(params), METRIC_CHUNK_SIZE):
        async with db.transaction() as tx:
            await tx.executemany(_UPSERT_METRIC_SQL, params[i : i + METRIC_CHUNK_SIZE])
    return len(params), skipped
```

- [ ] **Step 8: Create `src/cs/core/repo/__init__.py`**

```python
from __future__ import annotations

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
from cs.core.repo.partitions import ensure_metric_partition

__all__ = [
    "count_creatives",
    "creative_has_asset",
    "creative_has_primary_asset",
    "creative_metrics",
    "delete_assets_by_kind",
    "ensure_metric_partition",
    "get_ad_account",
    "get_asset_by_seq",
    "get_creative",
    "get_meta_connection",
    "get_meta_connection_by_id",
    "insert_asset",
    "list_active_ad_account_ids",
    "list_ad_accounts",
    "list_assets_by_kind",
    "list_creatives",
    "list_ready_meta_connections_with_active_account",
    "set_ad_account_status",
    "set_meta_connection_status",
    "set_primary_asset",
    "upsert_ad",
    "upsert_ad_account",
    "upsert_creative",
    "upsert_meta_connection",
    "upsert_metrics",
]
```

- [ ] **Step 9: Verify the new package imports cleanly**

Run: `python -c "from cs.core import repo; print(sorted(repo.__all__))"`
Expected: prints the 25-name list above with no import errors.

- [ ] **Step 10: Run the full test suite to confirm nothing is disturbed yet**

Run: `pytest -q`
Expected: same pass count as before this task (no callers touched, `cs.ingest.repo` still the
only thing anyone imports).

- [ ] **Step 11: Commit**

```bash
git add src/cs/core/repo/
git commit -m "feat(core): add cs.core.repo package, split by entity (unused so far)"
```

---

### Task 2: Cut every caller over to `cs.core.repo`, delete the old files

**Files:**
- Delete: `src/cs/ingest/repo.py`, `src/cs/ingest/partitions.py`
- Modify (bulk pattern -- see Step 1): `src/cs/insights/copilot.py`, `src/cs/tagging/jobs.py`,
  `src/cs/tagging/runner.py`, `src/cs/api/routers/tags.py`, `src/cs/api/routers/accounts.py`,
  `src/cs/api/routers/connections.py`, `src/cs/api/routers/creatives.py`,
  `src/cs/ingest/service.py`, `tests/insights/seed.py`, `tests/ingest/test_jobs.py`,
  `tests/tagging/test_reconcile.py`, `tests/ingest/test_repo.py`, `tests/tagging/test_repo.py`,
  `tests/tagging/test_runner.py`, `tests/tagging/test_embed.py`,
  `tests/api/test_accounts_and_creatives.py`, `tests/tagging/test_batch.py`,
  `tests/api/test_tags.py`, `tests/e2e/test_loop.py`
- Modify (manual -- see Steps 2-4): `tests/ingest/test_service.py`, `scripts/seed_dev_fivetran.py`,
  `src/cs/ingest/jobs.py`
- Move: `tests/ingest/test_partitions.py` -> `tests/core/test_repo_partitions.py`
  (create `tests/core/__init__.py` if the directory doesn't already exist as a package -- check
  first with `ls tests/core/` before assuming)

**Interfaces:**
- Consumes: `cs.core.repo` from Task 1 (already built, already verified to import cleanly).
- Produces: no `src/` or `tests/` file references `cs.ingest.repo` or `cs.ingest.partitions`
  anywhere after this task.

- [ ] **Step 1: Bulk-update the safe-pattern files**

These files each have `from cs.ingest import repo` as its own complete import statement --
either bare or with `as alias` -- never combined with another module name on the same line.
Verified safe: none of these lines match `service` or another name after `repo`.

```bash
FILES="src/cs/insights/copilot.py src/cs/tagging/jobs.py src/cs/tagging/runner.py \
  src/cs/api/routers/tags.py src/cs/api/routers/accounts.py src/cs/api/routers/connections.py \
  src/cs/api/routers/creatives.py src/cs/ingest/service.py tests/insights/seed.py \
  tests/ingest/test_jobs.py tests/tagging/test_reconcile.py tests/ingest/test_repo.py \
  tests/tagging/test_repo.py tests/tagging/test_runner.py tests/tagging/test_embed.py \
  tests/api/test_accounts_and_creatives.py tests/tagging/test_batch.py tests/api/test_tags.py \
  tests/e2e/test_loop.py"

for f in $FILES; do
  sed -i '' \
    -e 's/^from cs\.ingest import repo$/from cs.core import repo/' \
    -e 's/^from cs\.ingest import repo as /from cs.core import repo as /' \
    "$f"
done
```

Verify every targeted line actually changed:
```bash
grep -rn "^from cs\.ingest import repo\b" $FILES
```
Expected: no output (every matching line was rewritten).

- [ ] **Step 2: Manually fix `tests/ingest/test_service.py`**

Its import line combines `repo` with `service` (which is *not* moving in this task -- that's
Task 3), so the bulk pattern above would incorrectly move `service` to `cs.core` too. Change:

```python
from cs.ingest import repo, service
```
to:
```python
from cs.core import repo
from cs.ingest import service
```

- [ ] **Step 3: Manually fix `scripts/seed_dev_fivetran.py`**

This uses the submodule-path import style, not `from cs.ingest import repo`:
```python
from cs.ingest.repo import upsert_meta_connection
```
to:
```python
from cs.core.repo import upsert_meta_connection
```

- [ ] **Step 4: Manually fix `src/cs/ingest/jobs.py`**

Its import line combines four names. Change:
```python
from cs.ingest import fivetran_insights, fivetran_transform, repo, service
```
to:
```python
from cs.core import repo
from cs.ingest import fivetran_insights, fivetran_transform, service
```
(The remaining three names move in Tasks 3 and 5.)

- [ ] **Step 5: Move the partitions test**

```bash
mkdir -p tests/core
ls tests/core/__init__.py 2>/dev/null || touch tests/core/__init__.py
git mv tests/ingest/test_partitions.py tests/core/test_repo_partitions.py
```

Edit `tests/core/test_repo_partitions.py`, changing:
```python
from cs.ingest.partitions import ensure_metric_partition
```
to:
```python
from cs.core.repo.partitions import ensure_metric_partition
```

- [ ] **Step 6: Delete the old files**

```bash
git rm src/cs/ingest/repo.py src/cs/ingest/partitions.py
```

- [ ] **Step 7: Confirm nothing still references the old paths**

Run: `grep -rn "cs\.ingest\.repo\|cs\.ingest\.partitions\|cs\.ingest import repo\|cs\.ingest import partitions" src tests scripts`
Expected: no output.

- [ ] **Step 8: Run the full test suite**

Run: `pytest -q`
Expected: all pass, same count as Task 1's Step 10 baseline (module names changed, behavior
didn't).

- [ ] **Step 9: Lint**

Run: `ruff check --fix . && ruff format .`
Expected: clean (this also normalizes any import ordering left imperfect by the manual edits
above).

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: move cs.ingest.repo/partitions to cs.core.repo, split by entity"
```

---

### Task 3: Rename `service.py` to `assets.py`

**Files:**
- Move: `src/cs/ingest/service.py` -> `src/cs/ingest/assets.py`
- Move: `tests/ingest/test_service.py` -> `tests/ingest/test_assets.py`
- Modify: `src/cs/ingest/jobs.py`

**Interfaces:**
- Consumes: nothing new (this module's content -- `download_creative`, `download_carousel` --
  is unchanged; only its filename and callers' import lines change).
- Produces: `cs.ingest.assets.download_creative(...)`, `cs.ingest.assets.download_carousel(...)`
  -- same signatures as `cs.ingest.service` had.

- [ ] **Step 1: Move the module**

```bash
git mv src/cs/ingest/service.py src/cs/ingest/assets.py
```
No content changes needed -- `service.py`'s only import (`from cs.ingest import repo`) was
already rewritten to `from cs.core import repo` in Task 2's bulk step.

- [ ] **Step 2: Move and update the test**

```bash
git mv tests/ingest/test_service.py tests/ingest/test_assets.py
```
In `tests/ingest/test_assets.py`, change:
```python
from cs.ingest import assets
```
(replacing whatever `from cs.ingest import service` / split-out `service` import Task 2 left --
by this point the file has `from cs.core import repo` on its own line and `from cs.ingest import
service` on another; change only the second to `from cs.ingest import assets`.)

Then update the two call sites:
```python
        await service.download_creative(
```
to:
```python
        await assets.download_creative(
```
and:
```python
        await service.download_carousel(
```
to:
```python
        await assets.download_carousel(
```

- [ ] **Step 3: Update `src/cs/ingest/jobs.py`**

Change:
```python
from cs.ingest import fivetran_insights, fivetran_transform, service
```
to:
```python
from cs.ingest import assets, fivetran_insights, fivetran_transform
```
and update the two call sites:
```python
        await service.download_creative(ctx, http, creative_id, p["asset_url"], p["kind"])
```
to:
```python
        await assets.download_creative(ctx, http, creative_id, p["asset_url"], p["kind"])
```
and:
```python
        await service.download_carousel(ctx, http, creative_id, p["asset_urls"])
```
to:
```python
        await assets.download_carousel(ctx, http, creative_id, p["asset_urls"])
```

- [ ] **Step 4: Confirm nothing still references the old module name**

Run: `grep -rn "cs\.ingest\.service\|cs\.ingest import service\|\bservice\.download_" src tests`
Expected: no output.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 6: Lint**

Run: `ruff check --fix . && ruff format .`

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: rename cs.ingest.service to cs.ingest.assets"
```

---

### Task 4: Build `src/cs/ingest/fivetran/`

**Files:**
- Create: `src/cs/ingest/fivetran/__init__.py`, `src/cs/ingest/fivetran/schema.py`,
  `src/cs/ingest/fivetran/transform.py`, `src/cs/ingest/fivetran/insights.py`,
  `src/cs/ingest/fivetran/fixtures.py`
- Test: none touched yet -- `src/cs/ingest/fivetran_schema.py`, `fivetran_transform.py`,
  `fivetran_insights.py`, `fivetran_fixtures.py` remain in place and in exclusive use.

**Interfaces:**
- Produces: `cs.ingest.fivetran.schema` (same names as `cs.ingest.fivetran_schema`:
  `CAMPAIGN_HISTORY`, `AD_HISTORY`, `CREATIVE_HISTORY`, `AD_VIDEO_HISTORY`, `ENTITY_TABLES`,
  `AD_INSIGHTS`, `AD_INSIGHTS_ACTIONS`, `AD_INSIGHTS_ACTION_VALUES`,
  `AD_INSIGHTS_VIDEO_PLAY_ACTIONS`, `AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS`,
  `AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS`, `AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS`,
  `INSIGHTS_ACTION_TABLES`, `ALL_TABLES`); `cs.ingest.fivetran.transform.discover_and_sync_entities(ctx, org_id, connection) -> dict`;
  `cs.ingest.fivetran.insights.sync_insights(ctx, org_id, connection) -> dict`;
  `cs.ingest.fivetran.fixtures.create_fixture_schema/insert_campaign_ad_creative/insert_video/insert_daily_insight`
  -- all identical signatures to today's flat modules.

- [ ] **Step 1: Create `src/cs/ingest/fivetran/schema.py`** (moved verbatim from `src/cs/ingest/fivetran_schema.py`)

```python
"""Single source of truth for which Fivetran-landed Meta Ads tables this app reads.

fivetran/transform.py, fivetran/insights.py, and fivetran/fixtures.py all reference these
names instead of their own string literals. A table not listed here (e.g. ad_set_history)
may still be landed by Fivetran's facebook_ads connector, but nothing in this app ever reads
it -- a candidate for disabling on the connector's schema config.
"""

from __future__ import annotations

CAMPAIGN_HISTORY = "campaign_history"
AD_HISTORY = "ad_history"
CREATIVE_HISTORY = "creative_history"
AD_VIDEO_HISTORY = "ad_video_history"

ENTITY_TABLES = (CAMPAIGN_HISTORY, AD_HISTORY, CREATIVE_HISTORY, AD_VIDEO_HISTORY)

AD_INSIGHTS = "ad_insights"

AD_INSIGHTS_ACTIONS = "ad_insights_actions"
AD_INSIGHTS_ACTION_VALUES = "ad_insights_action_values"
AD_INSIGHTS_VIDEO_PLAY_ACTIONS = "ad_insights_video_play_actions"
AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS = "ad_insights_video_thruplay_watched_actions"
AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS = "ad_insights_video_p50_watched_actions"
AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS = "ad_insights_video_p100_watched_actions"

INSIGHTS_ACTION_TABLES = (
    AD_INSIGHTS_ACTIONS,
    AD_INSIGHTS_ACTION_VALUES,
    AD_INSIGHTS_VIDEO_PLAY_ACTIONS,
    AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS,
)

ALL_TABLES = ENTITY_TABLES + (AD_INSIGHTS,) + INSIGHTS_ACTION_TABLES
```

- [ ] **Step 2: Create `src/cs/ingest/fivetran/transform.py`**, with the `_latest_rows` dedup
  extraction (this is the one place logic, not just location, changes -- see spec section 6)

```python
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import queue
from cs.core import repo
from cs.ingest.fivetran.schema import (
    AD_HISTORY,
    AD_VIDEO_HISTORY,
    CAMPAIGN_HISTORY,
    CREATIVE_HISTORY,
)
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.ingest")


def _q(schema: str, table: str) -> str:
    # schema is always schema_for(org_id)'s output -- [a-z0-9_]+ only -- so this is not an
    # injection risk; it's still double-quoted because a schema starting with a digit
    # (org_id is a UUID) is not a bare-word-safe Postgres identifier.
    return f'"{schema}".{table}'


async def _latest_rows(db, schema: str, table: str) -> list[asyncpg.Record]:
    """Fivetran's *_history tables carry one row per sync per id (a change log, not a
    snapshot) -- this collapses to the most recently synced row per id, the same pattern
    three separate call sites used to inline."""
    return await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, table)}
        ) t WHERE rn = 1
    """)


def _carousel_urls(raw: str | None) -> list[str] | None:
    """object_story_link_data_child_attachments -> picture URLs, same shape parse.py's
    _carousel_card_urls expected from the live Graph API's link_data.child_attachments."""
    if not raw:
        return None
    try:
        cards = json.loads(raw)
        if not isinstance(cards, list):
            return None  # malformed/unexpected JSON shape -- not a list of cards
        urls = [c["picture"] for c in cards if isinstance(c, dict) and c.get("picture")]
    except (TypeError, ValueError):
        return None
    return urls or None


def _pid(value: object) -> str | None:
    """Fivetran lands Meta's numeric IDs (account_id, id, campaign_id, ad_set_id,
    creative_id, ...) as bigint -- confirmed against a real connector, not assumed --
    but every platform-id field downstream (ad_account.platform_account_id,
    ad.platform_ad_id, PlatformAd/PlatformCreative, dict keys used to join ads to
    creatives/accounts) is text. asyncpg refuses to bind an int for a text column
    parameter, so every id read from a landed table must be stringified before use.
    None passes through unchanged -- campaign_id/ad_set_id can be genuinely absent on
    some ad types."""
    return None if value is None else str(value)


def _format_and_urls(
    row: asyncpg.Record, videos_by_id: dict[str, str]
) -> tuple[str, str | None, list[str] | None]:
    if row["video_id"]:
        # ad_video_history.source is the actual downloadable MP4 -- confirmed against a
        # real connector. Some videos land with no source yet (still processing on
        # Meta's side, or the row hasn't synced): videos_by_id.get returns None then,
        # same as any other creative whose asset isn't downloadable yet -- the existing
        # asset_url-gated enqueue below already skips cleanly in that case.
        return "video", videos_by_id.get(_pid(row["video_id"])), None
    child_urls = _carousel_urls(row["object_story_link_data_child_attachments"])
    if child_urls:
        return "carousel", None, child_urls
    return "image", row["image_url"] or row["thumbnail_url"], None


async def _discover_accounts(db, org_id: UUID, connection_id: UUID, schema: str) -> dict[str, UUID]:
    """Every distinct account_id seen in campaign_history becomes (or already is) an
    ad_account row, starting 'paused' until a human activates it -- same default the old
    OAuth callback used."""
    rows = await db.fetch(f"SELECT DISTINCT account_id FROM {_q(schema, CAMPAIGN_HISTORY)}")
    by_platform_id: dict[str, UUID] = {}
    for r in rows:
        pid = _pid(r["account_id"])
        if pid is None:
            continue
        aid = await repo.upsert_ad_account(db, org_id, pid, pid, "USD", connection_id)
        by_platform_id[pid] = aid
    return by_platform_id


async def _sync_ads_and_creatives(
    db, org_id: UUID, schema: str, accounts_by_platform_id: dict[str, UUID]
) -> dict[str, int]:
    creative_rows = await _latest_rows(db, schema, CREATIVE_HISTORY)
    creatives_by_platform_id: dict[str, asyncpg.Record] = {_pid(r["id"]): r for r in creative_rows}

    ad_rows = await _latest_rows(db, schema, AD_HISTORY)

    video_rows = await _latest_rows(db, schema, AD_VIDEO_HISTORY)
    videos_by_id: dict[str, str] = {
        pid: r["source"] for r in video_rows if r["source"] and (pid := _pid(r["id"]))
    }

    new_creatives = 0
    synced_ads = 0
    failed = 0
    for ad_row in ad_rows:
        try:
            acct_id = accounts_by_platform_id.get(_pid(ad_row["account_id"]))
            if acct_id is None:
                continue  # not discovered yet, or discovered but not activated by the user
            cr = creatives_by_platform_id.get(_pid(ad_row["creative_id"]))
            if cr is None:
                continue  # ad references a creative Fivetran hasn't landed yet
            fmt, asset_url, child_urls = _format_and_urls(cr, videos_by_id)
            platform_creative = PlatformCreative(
                platform_creative_id=_pid(cr["id"]),
                format=fmt,
                headline=cr["title"],
                primary_text=cr["body"],
                description=None,
                cta_type=cr["call_to_action_type"],
                link_url=cr["object_story_link_data_link"],
                asset_url=asset_url,
                asset_mime="image/jpeg" if asset_url else None,
                child_asset_urls=child_urls,
                raw=dict(cr),
            )
            creative_id, created = await repo.upsert_creative(
                db, org_id, acct_id, platform_creative, raw_path=""
            )
            new_creatives += int(created)

            platform_ad = PlatformAd(
                platform_ad_id=_pid(ad_row["id"]),
                name=ad_row["name"] or "",
                status=ad_row["status"] or "",
                adset_id=_pid(ad_row["ad_set_id"]),
                campaign_id=_pid(ad_row["campaign_id"]),
                platform_creative_id=_pid(cr["id"]),
            )
            await repo.upsert_ad(db, org_id, acct_id, platform_ad, creative_id)
            synced_ads += 1

            kind = "original_video" if fmt == "video" else "original_image"
            if fmt == "carousel" and child_urls:
                if not await repo.creative_has_primary_asset(db, org_id, creative_id):
                    await queue.enqueue(
                        db,
                        org_id,
                        "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": child_urls},
                        dedup_key=str(creative_id),
                    )
            elif asset_url and not await repo.creative_has_asset(db, org_id, creative_id, kind):
                await queue.enqueue(
                    db,
                    org_id,
                    "meta.download_creative",
                    {"creative_id": str(creative_id), "asset_url": asset_url, "kind": kind},
                    dedup_key=str(creative_id),
                )
        except Exception:
            # One malformed row (bad JSON, unexpected null, etc.) must not abort the
            # whole org's sync -- log it and keep going, same fault isolation the old
            # service.sync_account's per-creative try/except gave us.
            failed += 1
            log.exception(
                "failed to sync ad row",
                extra={"org_id": str(org_id), "platform_ad_id": ad_row["id"]},
            )

    return {"new_creatives": new_creatives, "synced_ads": synced_ads, "failed": failed}


async def discover_and_sync_entities(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    accounts_by_platform_id = await _discover_accounts(ctx.db, org_id, connection["id"], schema)
    # Discovery lists every account Fivetran can see (so the user has something to pick
    # from in Settings), but ingestion -- ads, creatives, asset downloads, tagging -- only
    # ever runs for accounts the user has explicitly activated. A still-paused account
    # gets an ad_account row and nothing else: no creative content is ever pulled or
    # exposed until the user consents to it in our own UI, independent of whatever Meta/
    # Fivetran-level account scope the connect flow granted.
    active_ids = await repo.list_active_ad_account_ids(ctx.db, org_id)
    active_by_platform_id = {
        pid: aid for pid, aid in accounts_by_platform_id.items() if aid in active_ids
    }
    counts = await _sync_ads_and_creatives(ctx.db, org_id, schema, active_by_platform_id)
    return {"accounts_discovered": len(accounts_by_platform_id), **counts}
```

Note: `monkeypatch.setattr(fivetran_transform.repo, "upsert_creative", ...)` in
`test_fivetran_transform.py` (Task 5) still works after this -- `from cs.core import repo` gives
the module a `repo` attribute exactly like `from cs.ingest import repo` did, just pointing at the
new package.

- [ ] **Step 3: Create `src/cs/ingest/fivetran/insights.py`**, with the breakdown-table-loop
  dedup extraction (the second, and last, logic-shape change -- see spec section 6)

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow
from cs.core import repo
from cs.ingest.fivetran.schema import (
    AD_INSIGHTS,
    AD_INSIGHTS_ACTION_VALUES,
    AD_INSIGHTS_ACTIONS,
    AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS,
    AD_INSIGHTS_VIDEO_PLAY_ACTIONS,
    AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS,
)
from cs.jobs.registry import JobContext

# Ported from the deleted cs.connectors.meta.parse.CONVERSION_ACTION_TYPES -- the business
# logic (which action_type counts as a conversion) is unchanged; only the source shape is.
CONVERSION_ACTION_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
    "onsite_conversion.lead_grouped",
)

# key -> Fivetran table; sync_insights loops over this instead of six hand-written
# _action_totals() calls. Keys are chosen to match the MetricRow field each populates.
_BREAKDOWN_TABLES = {
    "actions": AD_INSIGHTS_ACTIONS,
    "action_values": AD_INSIGHTS_ACTION_VALUES,
    "video_3s": AD_INSIGHTS_VIDEO_PLAY_ACTIONS,
    "video_thruplay": AD_INSIGHTS_VIDEO_THRUPLAY_WATCHED_ACTIONS,
    "video_p50": AD_INSIGHTS_VIDEO_P50_WATCHED_ACTIONS,
    "video_p100": AD_INSIGHTS_VIDEO_P100_WATCHED_ACTIONS,
}


def _q(schema: str, table: str) -> str:
    return f'"{schema}".{table}'


def _pid(value: object) -> str | None:
    """Meta's ad_id lands as bigint (confirmed against a real connector for the entity
    tables; ad_insights follows the same convention) but ad.platform_ad_id -- what this
    joins against -- is text. Without this, every metric row would silently fail to
    match any ad (int != str in a dict lookup) and get counted as skipped rather than
    written, with no error raised."""
    return None if value is None else str(value)


async def _action_totals(db, schema: str, table: str) -> dict[tuple[str, date], dict[str, Decimal]]:
    """(ad_id, date) -> {action_type: value}, summed across `index` (Meta can report the
    same action_type more than once per breakdown row)."""
    rows = await db.fetch(f"""
        SELECT ad_id, date, action_type, sum(value) AS total
        FROM {_q(schema, table)} GROUP BY ad_id, date, action_type
    """)
    out: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in rows:
        bucket = out.setdefault((_pid(r["ad_id"]), r["date"]), {})
        bucket[r["action_type"]] = Decimal(str(r["total"]))
    return out


def _first_matching(by_type: dict[str, Decimal] | None, types: tuple[str, ...]) -> Decimal:
    if not by_type:
        return Decimal(0)
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _video_total(by_type: dict[str, Decimal] | None) -> int:
    return int(sum((by_type or {}).values()))


async def sync_insights(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    breakdowns = {
        key: await _action_totals(db, schema, table) for key, table in _BREAKDOWN_TABLES.items()
    }

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, AD_INSIGHTS)}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (_pid(r["ad_id"]), r["date"])
        publisher = r["publisher_platform"] or "unknown"
        position = r["platform_position"] or "unknown"
        metric_rows.append(
            MetricRow(
                platform_ad_id=_pid(r["ad_id"]),
                day=r["date"],
                placement=f"{publisher}:{position}",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=int(r["clicks"] or 0),
                link_clicks=int(r["inline_link_clicks"] or 0),
                conversions=_first_matching(breakdowns["actions"].get(key), CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(
                    breakdowns["action_values"].get(key), CONVERSION_ACTION_TYPES
                ),
                video_3s_views=_video_total(breakdowns["video_3s"].get(key)),
                video_thruplay=_video_total(breakdowns["video_thruplay"].get(key)),
                video_p50=_video_total(breakdowns["video_p50"].get(key)),
                video_p100=_video_total(breakdowns["video_p100"].get(key)),
                raw=dict(r),
            )
        )

    # metric_daily is keyed per ad_account_id, but ad_insights doesn't carry one --
    # upsert_metrics resolves platform_ad_id -> (ad_account_id, creative_id) via the `ad`
    # table itself, so every ad in this org's insights is grouped by its actual account.
    by_account: dict[UUID, list[MetricRow]] = {}
    ad_rows = await db.fetch(
        "SELECT id, ad_account_id, platform_ad_id FROM ad WHERE org_id=$1", org_id
    )
    account_by_platform_ad_id = {a["platform_ad_id"]: a["ad_account_id"] for a in ad_rows}
    for m in metric_rows:
        acct_id = account_by_platform_ad_id.get(m.platform_ad_id)
        if acct_id is not None:
            by_account.setdefault(acct_id, []).append(m)

    written = skipped = 0
    for acct_id, rows in by_account.items():
        w, s = await repo.upsert_metrics(db, org_id, acct_id, rows)
        written += w
        skipped += s
    # Ads whose account we couldn't resolve at all (not in `by_account`) count as skipped too.
    skipped += len(metric_rows) - sum(len(v) for v in by_account.values())
    return {"written": written, "skipped": skipped}
```

- [ ] **Step 4: Create `src/cs/ingest/fivetran/fixtures.py`** (moved verbatim from
  `src/cs/ingest/fivetran_fixtures.py`, only its `schema` import path changes)

```python
"""Fivetran-shaped fixture schema for local demo/e2e runs -- no live Fivetran connector or
Meta credentials needed. create_fixture_schema builds the tables fivetran/transform.py /
fivetran/insights.py read; callers insert their own rows into them."""

from __future__ import annotations

from datetime import date

from cs.core.db import Database
from cs.ingest.fivetran.schema import (
    AD_HISTORY,
    AD_INSIGHTS,
    AD_VIDEO_HISTORY,
    CAMPAIGN_HISTORY,
    CREATIVE_HISTORY,
    INSIGHTS_ACTION_TABLES,
)


async def create_fixture_schema(db: Database, schema: str) -> None:
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(f"""
        CREATE TABLE "{schema}".{CAMPAIGN_HISTORY} (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_HISTORY} (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{CREATIVE_HISTORY} (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT,
          object_story_link_data_child_attachments TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".{AD_VIDEO_HISTORY} (
          id TEXT, source TEXT, picture TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(
        f'CREATE TABLE "{schema}".{AD_INSIGHTS} (ad_id TEXT, date DATE, '
        f"publisher_platform TEXT, platform_position TEXT, spend NUMERIC, "
        f"impressions INT, clicks INT, inline_link_clicks INT)"
    )
    for t in INSIGHTS_ACTION_TABLES:
        await db.execute(
            f'CREATE TABLE "{schema}".{t} '
            f"(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)"
        )


async def insert_campaign_ad_creative(
    db: Database,
    schema: str,
    *,
    campaign_id: str,
    account_id: str,
    adset_id: str,
    ad_id: str,
    creative_id: str,
    name: str,
    headline: str,
    image_url: str,
    video_id: str | None = None,
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".campaign_history VALUES ($1, $2, $3, now())',
        campaign_id,
        account_id,
        f"Campaign {name}",
    )
    await db.execute(
        f'INSERT INTO "{schema}".ad_history VALUES ($1, $2, $3, $4, $5, $6, $7, now())',
        ad_id,
        account_id,
        adset_id,
        campaign_id,
        creative_id,
        name,
        "ACTIVE",
    )
    await db.execute(
        f'INSERT INTO "{schema}".creative_history VALUES '
        f"($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NULL, now())",
        creative_id,
        account_id,
        name,
        headline,
        "Primary text",
        "SHOP_NOW",
        image_url,
        None,
        "https://example.com",
        video_id,
    )


async def insert_video(db: Database, schema: str, *, video_id: str, source: str | None) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_video_history VALUES ($1, $2, NULL, now())',
        video_id,
        source,
    )


async def insert_daily_insight(
    db: Database,
    schema: str,
    *,
    ad_id: str,
    day: str,
    spend: float,
    impressions: int,
    clicks: int,
    link_clicks: int,
    placement: tuple[str, str] = ("facebook", "feed"),
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".ad_insights VALUES ($1, $2, $3, $4, $5, $6, $7, $8)',
        ad_id,
        date.fromisoformat(day),
        placement[0],
        placement[1],
        spend,
        impressions,
        clicks,
        link_clicks,
    )
```

- [ ] **Step 5: Create `src/cs/ingest/fivetran/__init__.py`** (empty, matching the existing
  `src/cs/ingest/__init__.py` and `src/cs/connectors/fivetran/__init__.py` convention)

```python
```

- [ ] **Step 6: Verify the new package imports cleanly**

Run:
```bash
python -c "from cs.ingest.fivetran import schema, transform, insights, fixtures; print('ok')"
```
Expected: `ok`, no import errors.

- [ ] **Step 7: Run the full test suite to confirm nothing is disturbed yet**

Run: `pytest -q`
Expected: same pass count as Task 3's baseline -- no callers touched yet, the old flat
`fivetran_*.py` files are still what everything uses.

- [ ] **Step 8: Commit**

```bash
git add src/cs/ingest/fivetran/
git commit -m "feat(ingest): add cs.ingest.fivetran package with dedup helpers (unused so far)"
```

---

### Task 5: Cut every caller over to `cs.ingest.fivetran`, delete the old flat files

**Files:**
- Delete: `src/cs/ingest/fivetran_schema.py`, `src/cs/ingest/fivetran_transform.py`,
  `src/cs/ingest/fivetran_insights.py`, `src/cs/ingest/fivetran_fixtures.py`
- Modify (bulk submodule-path pattern -- see Step 1): `tests/ingest/test_jobs.py`
- Modify (manual -- see Steps 2-5): `src/cs/ingest/jobs.py`,
  `tests/ingest/test_fivetran_transform.py`, `tests/ingest/test_fivetran_insights.py`,
  `tests/e2e/test_loop.py`, `scripts/seed_dev_fivetran.py`

**Interfaces:**
- Consumes: `cs.ingest.fivetran.{schema,transform,insights,fixtures}` from Task 4.
- Produces: no `src/` or `tests/` file references `cs.ingest.fivetran_schema`,
  `cs.ingest.fivetran_transform`, `cs.ingest.fivetran_insights`, or `cs.ingest.fivetran_fixtures`
  anywhere after this task.

- [ ] **Step 1: Bulk-update the one safe-pattern file**

`tests/ingest/test_jobs.py` only imports `fivetran_fixtures` via a direct submodule path, not
combined with anything else:
```bash
sed -i '' 's/cs\.ingest\.fivetran_fixtures/cs.ingest.fivetran.fixtures/' tests/ingest/test_jobs.py
```

- [ ] **Step 2: Manually fix `src/cs/ingest/jobs.py`**

Entering this task (after Task 3), its relevant import lines read:
```python
from cs.core import repo
from cs.ingest import assets, fivetran_insights, fivetran_transform
```
Change the second line to:
```python
from cs.ingest import assets
from cs.ingest.fivetran import insights, transform
```
Then update the two call sites:
```python
        entities = await fivetran_transform.discover_and_sync_entities(ctx, ctx.job.org_id, conn)
```
to:
```python
        entities = await transform.discover_and_sync_entities(ctx, ctx.job.org_id, conn)
```
and:
```python
        metrics = await fivetran_insights.sync_insights(ctx, ctx.job.org_id, conn)
```
to:
```python
        metrics = await insights.sync_insights(ctx, ctx.job.org_id, conn)
```

- [ ] **Step 3: Manually fix `tests/ingest/test_fivetran_transform.py`**

Entering this task (after Task 2), its import lines read:
```python
from cs.core import repo
from cs.ingest import fivetran_transform
from cs.ingest.fivetran_fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_video,
)
```
Change to (a bare module alias -- this file calls `fivetran_transform.discover_and_sync_entities`
and `fivetran_transform._carousel_urls`/`fivetran_transform.repo` by attribute throughout, so
aliasing the module itself covers every one of the ~15 call sites with no further edits):
```python
from cs.core import repo
from cs.ingest.fivetran import transform as fivetran_transform
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_video,
)
```

- [ ] **Step 4: Manually fix `tests/ingest/test_fivetran_insights.py`**

Entering this task (after Task 2), its import lines read:
```python
from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import repo
from cs.ingest import fivetran_insights
from cs.ingest.fivetran_fixtures import create_fixture_schema, insert_daily_insight
```
Change to:
```python
from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import repo
from cs.ingest.fivetran import insights as fivetran_insights
from cs.ingest.fivetran.fixtures import create_fixture_schema, insert_daily_insight
```

- [ ] **Step 5: Manually fix `tests/e2e/test_loop.py` and `scripts/seed_dev_fivetran.py`**

In `tests/e2e/test_loop.py`, change:
```python
from cs.ingest import fivetran_insights, fivetran_transform
```
to:
```python
from cs.ingest.fivetran import insights as fivetran_insights, transform as fivetran_transform
```
and change:
```python
from cs.ingest.fivetran_fixtures import (
```
to:
```python
from cs.ingest.fivetran.fixtures import (
```
(the `repo` import in this file was already handled in Task 2's bulk step).

In `scripts/seed_dev_fivetran.py`, change:
```python
from cs.ingest.fivetran_fixtures import (
```
to:
```python
from cs.ingest.fivetran.fixtures import (
```
and change:
```python
from cs.ingest.fivetran_transform import discover_and_sync_entities
```
to:
```python
from cs.ingest.fivetran.transform import discover_and_sync_entities
```
(the `repo` import in this file was already handled in Task 2's Step 3).

- [ ] **Step 6: Delete the old flat files**

```bash
git rm src/cs/ingest/fivetran_schema.py src/cs/ingest/fivetran_transform.py \
  src/cs/ingest/fivetran_insights.py src/cs/ingest/fivetran_fixtures.py
```

- [ ] **Step 7: Confirm nothing still references the old paths**

Run:
```bash
grep -rn "fivetran_schema\|fivetran_transform\|fivetran_insights\|fivetran_fixtures" src tests scripts \
  | grep -v "src/cs/ingest/fivetran/"
```
Expected: no output, or only docstring prose in `src/cs/connectors/fivetran/connect.py` (its
mention of `fivetran_transform.py` is descriptive text, not an import -- optionally update it to
say `fivetran/transform.py` for accuracy while you're here, but it's not required for
correctness).

- [ ] **Step 8: Run the full test suite**

Run: `pytest -q`
Expected: all pass, same count as Task 4's baseline.

- [ ] **Step 9: Lint**

Run: `ruff check --fix . && ruff format .`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: move cs.ingest.fivetran_* flat modules into cs.ingest.fivetran package"
```

---

### Task 6: Final verification

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Full test suite**

Run: `pytest -q`
Expected: all pass.

- [ ] **Step 2: Full lint**

Run: `ruff check . && ruff format --check .`
Expected: clean.

- [ ] **Step 3: Confirm the new layout matches the spec**

Run:
```bash
find src/cs/core/repo src/cs/ingest/fivetran -name "*.py" | sort
ls src/cs/ingest/
```
Expected: matches spec section 3 exactly -- `core/repo/` has `__init__.py`, `accounts.py`,
`ads.py`, `assets.py`, `connections.py`, `creatives.py`, `metrics.py`, `partitions.py`;
`ingest/fivetran/` has `__init__.py`, `fixtures.py`, `insights.py`, `schema.py`, `transform.py`;
`ingest/` itself has `__init__.py`, `assets.py`, `fivetran/`, `jobs.py` and no leftover
`repo.py`, `partitions.py`, `service.py`, or flat `fivetran_*.py` files.

- [ ] **Step 4: Confirm no behavior changed**

Run: `git diff main --stat` (or the appropriate base ref) and skim it -- every hunk should be an
import line, a file move, or one of the two named dedup extractions (Task 4, Steps 2-3). Any
other kind of diff is a plan violation and should be investigated before calling this done.

- [ ] **Step 5: Final commit if Step 3's docstring touch-up (Task 5, Step 7) was made**

```bash
git add -A
git commit -m "docs: point connect.py's docstring at the new fivetran/transform.py path"
```
(Skip if that optional touch-up wasn't made.)
