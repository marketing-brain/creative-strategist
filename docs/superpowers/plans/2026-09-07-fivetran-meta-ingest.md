# Fivetran Meta Ingest Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct Meta OAuth + Graph API polling with a Fivetran-sourced ingest pipeline — connect via a Fivetran Connect Card (no Meta App Review required), land raw data in a per-org Postgres schema via a Fivetran Postgres destination pointed at this app's own Supabase database, and fold it into the existing `ad_account`/`ad`/`creative`/`asset`/`metric_daily` tables with a new transform job.

**Architecture:** A new `meta_connection` table (one per org) tracks the Fivetran connector and its health; `ad_account` rows are discovered from the landed data rather than created at connect time. `src/cs/connectors/fivetran/` holds the Fivetran REST client and the connect/schema logic; `src/cs/ingest/fivetran_transform.py` reads the landed tables via plain cross-schema SQL (same Postgres, no DuckDB) and reuses the existing `PlatformAd`/`PlatformCreative`/`MetricRow` dataclasses and `repo.upsert_*` functions unchanged. Two new job handlers (`meta.await_first_sync`, `meta.sync_from_fivetran`) replace `meta.backfill`/`meta.sync_incremental`. This plan assumes `docs/superpowers/plans/2026-09-06-drop-meta-publish.md` has already landed (no `publish_job` table, no write-side connector methods) but does not require it — apply that plan first if it hasn't shipped yet, since this plan deletes the connector module that plan only trims.

**Tech Stack:** FastAPI, asyncpg, httpx, respx (tests), dbmate migrations, pytest, Next.js, Vitest + Testing Library, Fivetran REST API (HTTP Basic auth).

**Spec:** `docs/superpowers/specs/2026-09-06-fivetran-meta-migration-design.md`

## Global Constraints

- Fivetran account: reuse the existing account (used by lp-console/Second Brain) — same `FIVETRAN_API_KEY`/`FIVETRAN_API_SECRET`, but a **new** destination group + Postgres destination dedicated to this product (the old S3/Delta Lake destination is dead — its backing AWS account was deleted).
- Fivetran destination: Postgres, pointed at this app's existing Supabase database, via Supabase's **session-mode pooler (port 5432)**, not the transaction-mode pooler (port 6543) — Fivetran issues DDL during sync. This is a one-time manual setup in the Fivetran dashboard (create the destination, grant it a Postgres role scoped to `CREATE SCHEMA`/write only within schemas it creates), not something this plan's code configures.
- Every landed schema is named `schema_for(org_id)` = `{org_slug}__meta_ads`, where `org_slug` is `org_id` (a UUID) lowercased with every non-`[a-z0-9]` character collapsed to `_`. This function has exactly one implementation (`src/cs/connectors/fivetran/connect.py`); nothing else re-derives it — drift here silently reads the wrong (or no) schema.
- Historical depth (~180 days) and re-sync cadence are configured on the Fivetran connector itself, not in this app's code — the transform is a full per-org "replace with current truth" pass, not an incremental window.
- No Meta App Review is required anywhere after this plan — Fivetran's own already-reviewed app requests the read-only scopes.
- All Phase 0-5 constraints from `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` apply (org-scoped queries, admin-gated consequential routes, raw-page-before-parse discipline where it still applies).

---

## File structure

```
src/cs/connectors/fivetran/
  __init__.py
  client.py                # Fivetran REST client: create/get/delete connector, find by schema
  connect.py                # schema_for, start_connection, finalize_connection
db/migrations/20260907000001_meta_connection.sql   # meta_connection table; ad_account: drop tokens, add meta_connection_id
src/cs/ingest/repo.py       # + meta_connection CRUD, upsert_ad_account signature change
src/cs/api/routers/connections.py   # POST /connections/meta/start, /finalize (replaces meta_oauth.py)
src/cs/api/routers/accounts.py      # activate/sync now enqueue meta.sync_from_fivetran
src/cs/api/app.py           # swap meta_oauth -> connections
web/src/app/fivetran-return/page.tsx        # popup return target
web/src/components/accounts-panel.tsx        # Connect Meta -> Fivetran popup flow
web/src/lib/api.ts / types.ts                # fivetranConnectStart/Finalize; Account gains connection fields
src/cs/ingest/fivetran_transform.py   # entity fold (campaigns/adsets/ads/creatives) + account discovery
src/cs/ingest/fivetran_insights.py    # custom-report fold -> MetricRow
src/cs/ingest/service.py    # rewritten: download_creative/download_carousel without a connector object
src/cs/ingest/jobs.py       # meta.await_first_sync, meta.sync_from_fivetran; nightly schedule updated
src/cs/connectors/meta/     # deleted entirely (oauth.py, client.py, connector.py, parse.py, scrub.py)
src/cs/connectors/base.py   # AdPlatformConnector Protocol removed; PlatformAd/PlatformCreative/MetricRow kept
src/cs/connectors/fakes.py  # deleted
src/cs/core/crypto.py       # deleted (no more caller once oauth.py is gone)
src/cs/core/config.py       # + fivetran_api_key/secret/group_id, web_base_url; - meta_app_id/secret/redirect_uri/api_version, token_encryption_key
scripts/seed_dev_fivetran.py   # replaces scripts/seed_dev_account.py for the demo/e2e loop
```

---

### Task 1: Fivetran REST client

**Files:**
- Create: `src/cs/connectors/fivetran/__init__.py`, `src/cs/connectors/fivetran/client.py`
- Modify: `src/cs/core/config.py` (add `fivetran_api_key`, `fivetran_api_secret`, `fivetran_group_id`, `web_base_url`)
- Test: `tests/connectors/test_fivetran_client.py`

**Interfaces:**
- Consumes: `Settings` (new fields above).
- Produces: `create_connector(settings, http, *, service, group_id, schema, redirect_uri) -> dict`, `get_connector(settings, http, connector_id) -> dict`, `delete_connector(settings, http, connector_id) -> None`, `find_connector_by_schema(settings, http, group_id, schema) -> str | None`, `FivetranError`. Task 3 builds on these.

- [ ] **Step 1: Add the new Settings fields**

In `src/cs/core/config.py`, add after `public_base_url`:

```python
    fivetran_api_key: str = ""
    fivetran_api_secret: str = ""
    fivetran_group_id: str = ""
    web_base_url: str = "http://localhost:3000"
```

(Leave `meta_app_id`/`meta_app_secret`/`meta_redirect_uri`/`meta_api_version`/`token_encryption_key` in place for now — Task 11 removes them once every caller is gone.)

- [ ] **Step 2: Write the failing test**

Create `tests/connectors/test_fivetran_client.py`:

```python
import httpx
import pytest
import respx

from cs.connectors.fivetran import client

F = "https://api.fivetran.com/v1"


def _settings(settings):
    settings.fivetran_api_key = "key"
    settings.fivetran_api_secret = "secret"
    return settings


@respx.mock
async def test_create_connector_returns_connect_card_url(settings):
    _settings(settings)
    respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "schema": "org1__meta_ads",
                    "connect_card": {"uri": "https://fivetran.com/connect-card/conn_1"},
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        data = await client.create_connector(
            settings, http, service="facebook_ads", group_id="grp_1",
            schema="org1__meta_ads", redirect_uri="https://app.example/fivetran-return",
        )
    assert data["id"] == "conn_1"
    assert data["connect_card"]["uri"] == "https://fivetran.com/connect-card/conn_1"
    sent = respx.calls.last.request
    assert sent.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_get_connector(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1", "schema": "org1__meta_ads"}})
    )
    async with httpx.AsyncClient() as http:
        data = await client.get_connector(settings, http, "conn_1")
    assert data["schema"] == "org1__meta_ads"


@respx.mock
async def test_delete_connector(settings):
    _settings(settings)
    route = respx.delete(f"{F}/connectors/conn_1").mock(return_value=httpx.Response(200, json={"data": {}}))
    async with httpx.AsyncClient() as http:
        await client.delete_connector(settings, http, "conn_1")
    assert route.called


@respx.mock
async def test_find_connector_by_schema_matches(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"items": [
                {"id": "conn_1", "schema": "org1__meta_ads"},
                {"id": "conn_2", "schema": "org2__meta_ads"},
            ]}},
        )
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found == "conn_2"


@respx.mock
async def test_find_connector_by_schema_no_match(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found is None


@respx.mock
async def test_error_response_raises_fivetran_error(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/bad").mock(
        return_value=httpx.Response(400, json={"message": "connector not found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(client.FivetranError, match="connector not found"):
            await client.get_connector(settings, http, "bad")
```

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/connectors/test_fivetran_client.py -v`
Expected: FAIL — `cs.connectors.fivetran` doesn't exist yet.

- [ ] **Step 4: Implement the client**

Create `src/cs/connectors/fivetran/__init__.py` (empty).

Create `src/cs/connectors/fivetran/client.py`:

```python
from __future__ import annotations

import base64

import httpx

from cs.core.config import Settings

API_BASE = "https://api.fivetran.com/v1"


class FivetranError(RuntimeError):
    pass


def _auth_header(settings: Settings) -> str:
    raw = f"{settings.fivetran_api_key}:{settings.fivetran_api_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


async def _call(
    settings: Settings, http: httpx.AsyncClient, method: str, path: str, json_body: dict | None = None
) -> dict:
    resp = await http.request(
        method,
        f"{API_BASE}{path}",
        json=json_body,
        headers={
            "Authorization": _auth_header(settings),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    body = (
        resp.json()
        if resp.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if resp.status_code >= 400:
        raise FivetranError(
            f"Fivetran {method} {path} failed ({resp.status_code}): {body.get('message', 'unknown error')}"
        )
    return body.get("data", {})


async def create_connector(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    service: str,
    group_id: str,
    schema: str,
    redirect_uri: str,
) -> dict:
    """Create a paused connector with an embedded Connect Card.

    The create response already contains connect_card.uri (the authorize URL) -- no
    second call needed.
    """
    return await _call(
        settings,
        http,
        "POST",
        "/connectors",
        {
            "service": service,
            "group_id": group_id,
            "run_setup_tests": False,
            "paused": True,
            "config": {"schema": schema, "pause_after_trial": True},
            "connect_card_config": {"redirect_uri": redirect_uri, "hide_setup_guide": True},
        },
    )


async def get_connector(settings: Settings, http: httpx.AsyncClient, connector_id: str) -> dict:
    return await _call(settings, http, "GET", f"/connectors/{connector_id}")


async def delete_connector(settings: Settings, http: httpx.AsyncClient, connector_id: str) -> None:
    await _call(settings, http, "DELETE", f"/connectors/{connector_id}")


async def find_connector_by_schema(
    settings: Settings, http: httpx.AsyncClient, group_id: str, schema: str
) -> str | None:
    """Single page (limit 1000) -- paginate if a group ever exceeds that."""
    data = await _call(settings, http, "GET", f"/groups/{group_id}/connectors?limit=1000")
    for c in data.get("items", []):
        if c.get("schema") == schema:
            return c["id"]
    return None
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/connectors/test_fivetran_client.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/connectors/fivetran src/cs/core/config.py tests/connectors/test_fivetran_client.py
git commit -m "feat: add Fivetran REST client"
```

---

### Task 2: `meta_connection` table; drop token columns from `ad_account`

**Files:**
- Create: `db/migrations/20260907000001_meta_connection.sql`
- Modify: `src/cs/ingest/repo.py` (`upsert_ad_account` signature, `list_ad_accounts` query, + new `meta_connection` functions)
- Modify (mechanical signature fix, see Step 4): `tests/insights/seed.py`, `tests/ingest/test_repo.py`, `tests/ingest/test_jobs.py`, `tests/tagging/test_embed.py`, `tests/tagging/test_reconcile.py`, `tests/tagging/test_repo.py`, `tests/tagging/test_batch.py`, `tests/tagging/test_runner.py`, `tests/api/test_tags.py`, `tests/api/test_accounts_and_creatives.py`, `tests/e2e/test_loop.py`
- Test: `tests/ingest/test_repo.py` (extended with `meta_connection` coverage)

**Interfaces:**
- Consumes: nothing new.
- Produces: `repo.upsert_ad_account(db, org_id, platform_account_id, name, currency, meta_connection_id=None) -> UUID` (token args removed). `repo.upsert_meta_connection(db, org_id, fivetran_connector_id, schema_name) -> UUID`, `repo.get_meta_connection(db, org_id) -> Record | None`, `repo.get_meta_connection_by_id(db, connection_id) -> Record | None`, `repo.set_meta_connection_status(db, connection_id, status, *, last_synced_at=None, last_error=None) -> None`, `repo.list_ready_meta_connections_with_active_account(db) -> list[Record]`. Task 3's `connect.py` and later job-handler tasks consume these.

- [ ] **Step 1: Write the migration**

```sql
-- migrate:up
CREATE TABLE meta_connection (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL UNIQUE REFERENCES org(id),
  fivetran_connector_id TEXT NOT NULL,
  schema_name TEXT NOT NULL,
  sync_status TEXT NOT NULL DEFAULT 'pending' CHECK (sync_status IN ('pending', 'syncing', 'ready', 'error')),
  last_synced_at TIMESTAMPTZ,
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE ad_account
  DROP COLUMN access_token_enc,
  DROP COLUMN token_expires_at,
  ADD COLUMN meta_connection_id UUID REFERENCES meta_connection(id);

-- migrate:down
ALTER TABLE ad_account
  DROP COLUMN meta_connection_id,
  ADD COLUMN access_token_enc TEXT NOT NULL DEFAULT '',
  ADD COLUMN token_expires_at TIMESTAMPTZ;

DROP TABLE meta_connection;
```

Save as `db/migrations/20260907000001_meta_connection.sql`.

- [ ] **Step 2: Apply it**

Run: `dbmate --url "$DATABASE_URL_TEST" --no-dump-schema --migrations-dir db/migrations up`
Expected: migration `20260907000001` applied.

- [ ] **Step 3: Update `repo.py`**

In `src/cs/ingest/repo.py`, replace `upsert_ad_account` (currently takes `token_enc`/`expires_at`) with:

```python
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
```

Replace `list_ad_accounts` with a version that joins connection health:

```python
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
```

Add, near the other `ad_account` functions:

```python
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

`datetime` needs importing at the top of `repo.py` if it isn't already (`from datetime import date, datetime` — check the existing import line; `datetime` is already imported per the current file).

- [ ] **Step 4: Fix every `upsert_ad_account` call site**

Every call site currently ends with `, encrypt_str(settings, "<token>"), None)`. Drop that tail (the new signature no longer takes those arguments) and drop the now-unused `from cs.core.crypto import encrypt_str` import in each file. Run:

```bash
grep -rl 'encrypt_str(settings, "[^"]*"), None' tests | xargs sed -i '' -E 's/, encrypt_str\(settings, "[^"]*"\), None\)/)/'
```

(On Linux, drop the `''` after `-i`.) This turns e.g. `db, org_id, "1", "Main", "USD", encrypt_str(settings, "t"), None,\n    )` into `db, org_id, "1", "Main", "USD",\n    )` — a plain trailing-comma call with the new 5-arg signature.

- [ ] **Step 5: Remove the now-dead `encrypt_str` imports and run lint's auto-fixer**

Run: `uv run ruff check --fix .`
Expected: ruff removes every now-unused `from cs.core.crypto import encrypt_str` line (F401) across the files touched in Step 4. `src/cs/core/crypto.py` and `tests/core/test_crypto.py` still exist and still use `encrypt_str`/`decrypt_str` internally, so ruff won't touch those.

- [ ] **Step 6: Write the new `meta_connection` repo tests**

Append to `tests/ingest/test_repo.py`:

```python
async def test_meta_connection_upsert_and_status(db, org_id):
    cid = await repo.upsert_meta_connection(db, org_id, "conn_1", "org1__meta_ads")
    row = await repo.get_meta_connection(db, org_id)
    assert row["id"] == cid and row["sync_status"] == "pending"

    await repo.set_meta_connection_status(db, cid, "error", last_error="broken")
    row = await repo.get_meta_connection_by_id(db, cid)
    assert row["sync_status"] == "error" and row["last_error"] == "broken"


async def test_meta_connection_upsert_is_idempotent_per_org(db, org_id):
    first = await repo.upsert_meta_connection(db, org_id, "conn_1", "org1__meta_ads")
    second = await repo.upsert_meta_connection(db, org_id, "conn_2", "org1__meta_ads")
    assert first == second
    row = await repo.get_meta_connection(db, org_id)
    assert row["fivetran_connector_id"] == "conn_2"
```

(If `tests/ingest/test_repo.py` doesn't already have an `org_id` fixture in scope, check what fixture the existing tests in that file use for an org id — mirror it rather than introducing a new one.)

- [ ] **Step 7: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: PASS. (`tests/api/test_accounts_and_creatives.py`'s `test_accounts_activate_enqueues_backfill` still asserts `job_type == "meta.backfill"` at this point — that's fixed in Task 4, not here; it should still pass since Task 4 hasn't touched `accounts.py` yet.)

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add meta_connection table; drop stored Meta OAuth tokens from ad_account"
```

---

### Task 3: Fivetran connect + schema logic

**Files:**
- Create: `src/cs/connectors/fivetran/connect.py`
- Test: `tests/connectors/test_fivetran_connect.py`

**Interfaces:**
- Consumes: `fivetran_client.create_connector`/`find_connector_by_schema`/`delete_connector`/`get_connector` (Task 1).
- Produces: `schema_for(org_id: UUID) -> str`, `start_connection(settings, http, org_id) -> dict` (`{"connect_card_url": str, "connector_id": str}`), `finalize_connection(settings, http, org_id, connector_id) -> dict` (the verified connector detail), `ConnectError(status, message)`. Task 4's router consumes these.

- [ ] **Step 1: Write the failing tests**

Create `tests/connectors/test_fivetran_connect.py`:

```python
from uuid import UUID

import pytest

from cs.connectors.fivetran import connect


def test_schema_for_is_stable_and_sanitized():
    org = UUID("01234567-89ab-cdef-0123-456789abcdef")
    schema = connect.schema_for(org)
    assert schema == "01234567_89ab_cdef_0123_456789abcdef__meta_ads"
    assert schema == connect.schema_for(org)  # deterministic


async def test_start_connection_deletes_existing_before_recreating(settings, monkeypatch):
    settings.fivetran_group_id = "grp_1"
    calls = []
    monkeypatch.setattr(
        connect.fivetran_client, "find_connector_by_schema",
        lambda s, h, g, sc: _record(calls, "find", "old_conn"),
    )
    monkeypatch.setattr(
        connect.fivetran_client, "delete_connector",
        lambda s, h, cid: _record(calls, "delete", None),
    )
    monkeypatch.setattr(
        connect.fivetran_client, "create_connector",
        lambda s, h, **kw: _record(
            calls, "create", {"id": "new_conn", "connect_card": {"uri": "https://card"}}
        ),
    )
    org = UUID(int=1)
    result = await connect.start_connection(settings, None, org)
    assert result == {"connect_card_url": "https://card", "connector_id": "new_conn"}
    assert [c[0] for c in calls] == ["find", "delete", "create"]


async def test_start_connection_requires_group_id(settings):
    settings.fivetran_group_id = ""
    with pytest.raises(connect.ConnectError) as exc:
        await connect.start_connection(settings, None, UUID(int=1))
    assert exc.value.status == 500


async def test_finalize_connection_accepts_matching_schema(settings, monkeypatch):
    org = UUID(int=1)
    monkeypatch.setattr(
        connect.fivetran_client, "get_connector",
        lambda s, h, cid: _async_result({"id": cid, "schema": connect.schema_for(org)}),
    )
    detail = await connect.finalize_connection(settings, None, org, "conn_1")
    assert detail["id"] == "conn_1"


async def test_finalize_connection_rejects_mismatched_schema(settings, monkeypatch):
    monkeypatch.setattr(
        connect.fivetran_client, "get_connector",
        lambda s, h, cid: _async_result({"id": cid, "schema": "someone_else__meta_ads"}),
    )
    with pytest.raises(connect.ConnectError) as exc:
        await connect.finalize_connection(settings, None, UUID(int=1), "conn_1")
    assert exc.value.status == 403


async def _async_result(value):
    return value


def _record(calls, name, value):
    calls.append((name, value))
    return _async_result(value) if not isinstance(value, dict) or True else _async_result(value)
```

Simplify `_record`'s odd conditional — it exists only to always wrap `value` in a coroutine via `_async_result`; write it plainly instead:

```python
def _record(calls, name, value):
    calls.append((name, value))
    return _async_result(value)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/connectors/test_fivetran_connect.py -v`
Expected: FAIL — `cs.connectors.fivetran.connect` doesn't exist yet.

- [ ] **Step 3: Implement `connect.py`**

```python
from __future__ import annotations

import re
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core.config import Settings

SERVICE = "facebook_ads"


class ConnectError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def schema_for(org_id: UUID) -> str:
    """{org_slug}__meta_ads -- the Postgres schema Fivetran lands this org's data into.

    Exactly one implementation in the whole app: the transform (fivetran_transform.py)
    imports this rather than re-deriving it, since drift here silently reads the wrong
    (or no) schema.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(org_id).lower()).strip("_")
    return f"{slug}__meta_ads"


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.web_base_url.rstrip('/')}/fivetran-return"


async def start_connection(settings: Settings, http: httpx.AsyncClient, org_id: UUID) -> dict:
    if not settings.fivetran_group_id:
        raise ConnectError(500, "FIVETRAN_GROUP_ID not configured")
    schema = schema_for(org_id)
    # Fivetran rejects a duplicate schema, so drop any existing connector for this org
    # (an abandoned attempt or a reconnect) before re-creating.
    existing = await fivetran_client.find_connector_by_schema(
        settings, http, settings.fivetran_group_id, schema
    )
    if existing:
        await fivetran_client.delete_connector(settings, http, existing)
    connector = await fivetran_client.create_connector(
        settings,
        http,
        service=SERVICE,
        group_id=settings.fivetran_group_id,
        schema=schema,
        redirect_uri=_redirect_uri(settings),
    )
    card = connector.get("connect_card") or {}
    if not card.get("uri"):
        raise ConnectError(502, "Fivetran returned no Connect Card URL")
    return {"connect_card_url": card["uri"], "connector_id": connector["id"]}


async def finalize_connection(
    settings: Settings, http: httpx.AsyncClient, org_id: UUID, connector_id: str
) -> dict:
    connector = await fivetran_client.get_connector(settings, http, connector_id)
    if connector.get("schema") != schema_for(org_id):
        raise ConnectError(403, "connection does not belong to you")
    return connector
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/connectors/test_fivetran_connect.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/connectors/fivetran/connect.py tests/connectors/test_fivetran_connect.py
git commit -m "feat: add Fivetran connect flow (schema ownership, connector lifecycle)"
```

---

### Task 4: `/connections/meta` routes; retire `/meta/oauth`; retarget `/accounts` jobs

**Files:**
- Create: `src/cs/api/routers/connections.py`
- Delete: `src/cs/api/routers/meta_oauth.py`, `src/cs/connectors/meta/oauth.py`, `tests/connectors/test_meta_oauth.py`, `tests/api/test_meta_oauth_routes.py`
- Modify: `src/cs/api/app.py`, `src/cs/api/routers/accounts.py`, `tests/api/test_accounts_and_creatives.py`
- Test: `tests/api/test_connections.py`

**Interfaces:**
- Consumes: `fivetran_connect.start_connection`/`finalize_connection`/`schema_for` (Task 3), `repo.upsert_meta_connection` (Task 2).
- Produces: `POST /connections/meta/start` -> `{"connect_card_url": str, "connector_id": str}`; `POST /connections/meta/finalize` -> `{"ok": true}` and enqueues `meta.await_first_sync` (handler added in Task 9 — the enqueue works today even before the handler exists, since jobs sit `pending` until a handler is registered and a worker claims them).

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_connections.py`:

```python
import httpx
import respx

from tests.conftest import dev_headers

H = dev_headers(org="org_conn")
F = "https://api.fivetran.com/v1"


async def test_start_requires_admin(client):
    r = await client.post(
        "/connections/meta/start", headers=dev_headers(org="org_conn", user="u2", role="member")
    )
    assert r.status_code == 403


@respx.mock
async def test_start_returns_connect_card_url(client, settings):
    settings.fivetran_group_id = "grp_1"
    respx.get(f"{F}/groups/grp_1/connectors").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"id": "conn_1", "schema": "x", "connect_card": {"uri": "https://card"}}},
        )
    )
    r = await client.post("/connections/meta/start", headers=H)
    assert r.status_code == 200
    assert r.json() == {"connect_card_url": "https://card", "connector_id": "conn_1"}


@respx.mock
async def test_finalize_persists_connection_and_enqueues_await_job(client, db):
    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    from cs.connectors.fivetran.connect import schema_for
    from uuid import UUID

    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "schema": schema_for(UUID(org_id))}}
        )
    )
    r = await client.post("/connections/meta/finalize", headers=H, json={"connector_id": "conn_1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = await db.fetchrow("SELECT * FROM meta_connection WHERE org_id=$1", UUID(org_id))
    assert row["fivetran_connector_id"] == "conn_1" and row["sync_status"] == "pending"
    assert await db.fetchval("SELECT job_type FROM job") == "meta.await_first_sync"


@respx.mock
async def test_finalize_rejects_connector_from_another_schema(client):
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1", "schema": "not_yours__meta_ads"}})
    )
    r = await client.post("/connections/meta/finalize", headers=H, json={"connector_id": "conn_1"})
    assert r.status_code == 403


async def test_finalize_requires_connector_id(client):
    r = await client.post("/connections/meta/finalize", headers=H, json={})
    assert r.status_code == 400
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/api/test_connections.py -v`
Expected: FAIL — the router doesn't exist yet.

- [ ] **Step 3: Implement the router**

Create `src/cs/api/routers/connections.py`:

```python
import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.connectors.fivetran import connect as fivetran_connect
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.queue import enqueue
from cs.ingest import repo

router = APIRouter(prefix="/connections/meta")


class FinalizeBody(BaseModel):
    connector_id: str


@router.post("/start")
async def start(
    auth: AuthContext = Depends(require_admin), settings: Settings = Depends(get_settings_dep)
) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            return await fivetran_connect.start_connection(settings, http, auth.org_id)
        except fivetran_connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc)) from None


@router.post("/finalize")
async def finalize(
    body: FinalizeBody,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            await fivetran_connect.finalize_connection(
                settings, http, auth.org_id, body.connector_id
            )
        except fivetran_connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc)) from None
    schema = fivetran_connect.schema_for(auth.org_id)
    conn_id = await repo.upsert_meta_connection(db, auth.org_id, body.connector_id, schema)
    await enqueue(
        db,
        auth.org_id,
        "meta.await_first_sync",
        {"meta_connection_id": str(conn_id)},
        dedup_key=str(conn_id),
        max_attempts=300,  # ~24h ceiling: exponential backoff caps at 5 minutes after ~9 attempts
    )
    return {"ok": True}
```

- [ ] **Step 4: Delete the old OAuth router and its tests**

```bash
git rm src/cs/api/routers/meta_oauth.py src/cs/connectors/meta/oauth.py tests/connectors/test_meta_oauth.py tests/api/test_meta_oauth_routes.py
```

- [ ] **Step 5: Swap the router registration in `app.py`**

In `src/cs/api/app.py`, change the import line from `meta_oauth,` to `connections,` (keep alphabetical order: `accounts, blobs, brand, connections, copilot, creatives, generation, health, insights, tags` — `meta_targets` is already gone per the publish-removal plan), and change `app.include_router(meta_oauth.router)` to `app.include_router(connections.router)`.

- [ ] **Step 6: Retarget the accounts router's job types**

In `src/cs/api/routers/accounts.py`, change `"meta.backfill"` (in `activate`) to `"meta.sync_from_fivetran"`, and `"meta.sync_incremental"` (in `sync`) to `"meta.sync_from_fivetran"`. Both routes' bodies are otherwise unchanged — they still flip `status` and enqueue a job keyed by `ad_account_id`, except `sync_from_fivetran`'s payload needs a `meta_connection_id`, not an `ad_account_id`, since the transform runs per-connection (org-scoped), not per-account. Rewrite both handlers:

```python
@router.post("/{account_id}/activate")
async def activate(
    account_id: UUID,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict:
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    await repo.set_ad_account_status(db, auth.org_id, account_id, "active")
    jid = None
    if acct["meta_connection_id"]:
        jid = await queue.enqueue(
            db,
            auth.org_id,
            "meta.sync_from_fivetran",
            {"meta_connection_id": str(acct["meta_connection_id"])},
            dedup_key=str(acct["meta_connection_id"]),
        )
    return {"job_id": str(jid) if jid else None}


@router.post("/{account_id}/sync")
async def sync(
    account_id: UUID,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict:
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    if not acct["meta_connection_id"]:
        raise HTTPException(409, "account has no Fivetran connection")
    jid = await queue.enqueue(
        db,
        auth.org_id,
        "meta.sync_from_fivetran",
        {"meta_connection_id": str(acct["meta_connection_id"])},
        dedup_key=str(acct["meta_connection_id"]),
    )
    return {"job_id": str(jid) if jid else None}
```

`repo.get_ad_account` already does `SELECT *`, so `acct["meta_connection_id"]` is available without any repo change.

- [ ] **Step 7: Fix `test_accounts_and_creatives.py`'s job-type assertion**

In `tests/api/test_accounts_and_creatives.py`, `test_accounts_activate_enqueues_backfill` currently asserts `await db.fetchval("SELECT job_type FROM job") == "meta.backfill"`. Since `_seed`'s `upsert_ad_account` call (already fixed to the new signature in Task 2) passes `meta_connection_id=None` by default, `activate` on a connection-less account now enqueues nothing (`jid` is `None`). Update the test:

```python
async def test_accounts_activate_enqueues_sync(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    from cs.ingest import repo as ingest_repo

    conn_id = await ingest_repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await db.execute("UPDATE ad_account SET meta_connection_id=$2 WHERE id=$1", acct, conn_id)
    r = await client.get("/accounts", headers=H)
    assert r.json()[0]["status"] == "paused"
    r = await client.post(f"/accounts/{acct}/activate", headers=H)
    assert r.status_code == 200 and r.json()["job_id"]
    assert await db.fetchval("SELECT job_type FROM job") == "meta.sync_from_fivetran"
    assert (await client.get("/accounts", headers=H)).json()[0]["status"] == "active"
```

(Rename the test function too — `test_accounts_activate_enqueues_backfill` -> `test_accounts_activate_enqueues_sync` — since "backfill" no longer exists as a concept.)

- [ ] **Step 8: Run the tests**

Run: `pytest tests/api/test_connections.py tests/api/test_accounts_and_creatives.py -v`
Expected: PASS

Run: `grep -rn "meta_oauth\|meta\.backfill\|meta\.sync_incremental" src tests`
Expected: no output.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: replace /meta/oauth with /connections/meta (Fivetran Connect Card)"
```

---

### Task 5: Frontend connect flow

**Files:**
- Create: `web/src/app/fivetran-return/page.tsx`
- Modify: `web/src/components/accounts-panel.tsx`, `web/src/lib/api.ts`, `web/src/lib/types.ts`, `web/tests/components/accounts-panel.test.tsx`

**Interfaces:**
- Consumes: `POST /connections/meta/start`, `POST /connections/meta/finalize` (Task 4).
- Produces: `api.fivetranConnectStart()`, `api.fivetranConnectFinalize(connectorId)`.

- [ ] **Step 1: Write the failing test**

Replace `web/tests/components/accounts-panel.test.tsx` with:

```tsx
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

const activate = vi.fn().mockResolvedValue({ job_id: 'j' });
const start = vi.fn().mockResolvedValue({ connect_card_url: 'https://card', connector_id: 'conn_1' });
const finalize = vi.fn().mockResolvedValue({ ok: true });
vi.mock('@/lib/api', () => ({
  api: { activateAccount: (id: string) => activate(id), syncAccount: vi.fn(), fivetranConnectStart: start, fivetranConnectFinalize: finalize },
}));

import { AccountsPanel } from '@/components/accounts-panel';

describe('AccountsPanel', () => {
  it('activates a paused account', async () => {
    render(<AccountsPanel accounts={[{ id: 'a1', platform: 'meta', platform_account_id: '1', name: 'Main', currency: 'USD', status: 'paused', sync_cursor: {}, connection_status: 'ready', connection_error: null }]} />);
    await userEvent.click(screen.getByRole('button', { name: /activate/i }));
    expect(activate).toHaveBeenCalledWith('a1');
    expect(await screen.findByText(/backfill queued/i)).toBeInTheDocument();
  });

  it('opens a popup and finalizes on the fivetran:return message', async () => {
    render(<AccountsPanel accounts={[]} />);
    const openSpy = vi.spyOn(window, 'open').mockReturnValue({ close: vi.fn() } as unknown as Window);
    await userEvent.click(screen.getByRole('button', { name: /connect meta/i }));
    expect(start).toHaveBeenCalled();
    expect(openSpy).toHaveBeenCalledWith('https://card', 'fivetran-connect', expect.any(String));

    window.dispatchEvent(new MessageEvent('message', { origin: window.location.origin, data: { type: 'fivetran:return' } }));
    await vi.waitFor(() => expect(finalize).toHaveBeenCalledWith('conn_1'));
    expect(await screen.findByText(/first sync finishes/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web && npx vitest run tests/components/accounts-panel.test.tsx`
Expected: FAIL — `AccountsPanel` still calls `metaOauthStart` and `window.location.assign`.

- [ ] **Step 3: Add the API client functions and types**

In `web/src/lib/api.ts`, replace the `metaOauthStart` line with:

```ts
  fivetranConnectStart: () => apiFetch<{ connect_card_url: string; connector_id: string }>('/connections/meta/start', { method: 'POST' }),
  fivetranConnectFinalize: (connectorId: string) => apiFetch<{ ok: boolean }>('/connections/meta/finalize', { method: 'POST', body: JSON.stringify({ connector_id: connectorId }) }),
```

In `web/src/lib/types.ts`, update the `Account` type to carry connection health:

```ts
export type Account = { id: string; platform: string; platform_account_id: string; name: string; currency: string; status: 'active' | 'paused' | 'disconnected'; sync_cursor: Record<string, unknown>; connection_status: 'pending' | 'syncing' | 'ready' | 'error' | null; connection_error: string | null };
```

- [ ] **Step 4: Create the popup-return page**

Create `web/src/app/fivetran-return/page.tsx`:

```tsx
'use client';
import { useEffect } from 'react';

// The Fivetran Connect Card redirects here (inside the popup) once the user finishes.
// Notify the opener so it can finalize, then close the popup. No auth required — this
// page carries no data of its own, it's just a signal back to the tab that opened it.
export default function FivetranReturn() {
  useEffect(() => {
    try {
      window.opener?.postMessage({ type: 'fivetran:return' }, window.location.origin);
    } finally {
      window.close();
    }
  }, []);

  return (
    <main className="flex min-h-screen items-center justify-center text-sm text-muted-foreground">
      You can close this window.
    </main>
  );
}
```

- [ ] **Step 5: Rewrite the connect flow in `accounts-panel.tsx`**

Replace the `connect` function in `web/src/components/accounts-panel.tsx`:

```tsx
  const connect = async () => {
    try {
      const { connect_card_url, connector_id } = await api.fivetranConnectStart();
      const popup = window.open(connect_card_url, 'fivetran-connect', 'width=500,height=700');
      const onMessage = async (e: MessageEvent) => {
        if (e.origin !== window.location.origin || e.data?.type !== 'fivetran:return') return;
        window.removeEventListener('message', onMessage);
        popup?.close();
        try {
          await api.fivetranConnectFinalize(connector_id);
          setMsg('Connected — accounts will appear once the first sync finishes.');
        } catch (err) {
          setMsg(`Failed to finish connecting: ${err instanceof Error ? err.message : 'Something went wrong.'}`);
        }
      };
      window.addEventListener('message', onMessage);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) {
        setMsg("You don't have permission to do this — ask an admin.");
      } else {
        setMsg(`Failed to connect: ${e instanceof Error ? e.message : 'Something went wrong.'}`);
      }
    }
  };
```

Also update the badge rendering to surface connection errors: where the account list renders `<Badge variant={statusVariant[a.status]}>{a.status}</Badge>`, add a second badge when `a.connection_status === 'error'`:

```tsx
                  <div className="flex items-center gap-1.5 text-muted-foreground">
                    <Badge variant={statusVariant[a.status]} className="capitalize">
                      {a.status}
                    </Badge>
                    {a.connection_status === 'error' && (
                      <Badge variant="destructive">{a.connection_error ?? 'Sync error'}</Badge>
                    )}
                    {a.sync_cursor?.last_synced_until ? `synced through ${String(a.sync_cursor.last_synced_until)}` : null}
                  </div>
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd web && npx vitest run tests/components/accounts-panel.test.tsx`
Expected: PASS

- [ ] **Step 7: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: connect Meta via a Fivetran Connect Card popup instead of a direct OAuth redirect"
```

---

### Task 6: Entity transform (campaigns, ad sets, ads, creatives, account discovery)

**Files:**
- Create: `src/cs/ingest/fivetran_transform.py`
- Test: `tests/ingest/test_fivetran_transform.py`

**Interfaces:**
- Consumes: `PlatformAd`, `PlatformCreative` (`src/cs/connectors/base.py`, unchanged), `repo.upsert_ad`, `repo.upsert_creative`, `repo.upsert_ad_account` (unchanged / Task 2's new signature).
- Produces: `discover_and_sync_entities(ctx, connection) -> dict` (summary counts). Task 9's `meta.sync_from_fivetran` handler calls this.

- [ ] **Step 1: Verify the landed schema against a real connector before writing SQL**

The column names below for `campaign_history`, `ad_set_history`, `ad_history`, and `creative_history` are taken from `lp_transform`'s working transform (`../lp_transform/src/lp_transform/transforms/meta_ads.py`), which reads these exact tables from a live `facebook_ads` connector today — high confidence. The **carousel-detection columns** (`object_story_link_data_child_attachments` on `creative_history`, and the `ad_asset_feed_spec_image` child table used to detect Dynamic Creative) are sourced from Fivetran's public dbt-source docs, not a live connector — lower confidence. Before running this task's SQL against production:

1. Create one real Fivetran `facebook_ads` connector (Task 3's `start_connection` against a test ad account, or directly in the Fivetran dashboard) pointed at the new Postgres destination.
2. Once it completes a first sync, run `\dt "<schema>".*` and `\d "<schema>".creative_history` against the destination database.
3. Confirm `creative_history` has an `object_story_link_data_child_attachments` column (or find its actual name/shape) and confirm whether `ad_asset_feed_spec_image` lands as its own table. Adjust Step 4's SQL to match if it differs — the fixture test in Step 5 defines its own schema, so it will pass regardless; this check is about trusting that fixture against reality before relying on it in production.

- [ ] **Step 2: Write the failing test**

Create `tests/ingest/test_fivetran_transform.py`. It builds a fixture schema shaped like a landed Fivetran connector, then asserts the transform folds it into `ad_account`/`ad`/`creative`:

```python
from uuid import UUID

from cs.ingest import fivetran_transform, repo
from cs.jobs.registry import JobContext

SCHEMA = "testorg__meta_ads"


async def _seed_fivetran_schema(db):
    await db.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{SCHEMA}"')
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".campaign_history (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".ad_history (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{SCHEMA}".creative_history (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT,
          object_story_link_data_child_attachments TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".campaign_history VALUES ('c1', '999', 'Campaign 1', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_set_history VALUES ('as1', '999', 'c1', 'Ad set 1', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_history
        VALUES ('ad1', '999', 'as1', 'c1', 'cr1', 'Ad 1', 'ACTIVE', now())
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".creative_history
        VALUES ('cr1', '999', 'name', 'Headline', 'Primary text', 'SHOP_NOW',
                'https://img/orig.jpg', 'https://img/thumb.jpg', 'https://dest.example',
                NULL, NULL, now())
    """)


async def test_discover_and_sync_entities(db, blobs, settings, org_id):
    await _seed_fivetran_schema(db)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert summary["accounts_discovered"] == 1
    accounts = await repo.list_ad_accounts(db, org_id)
    assert len(accounts) == 1 and accounts[0]["platform_account_id"] == "999"
    assert accounts[0]["status"] == "paused"

    ads = await db.fetch("SELECT * FROM ad WHERE org_id=$1", org_id)
    assert len(ads) == 1 and ads[0]["platform_ad_id"] == "ad1" and ads[0]["status"] == "ACTIVE"

    creatives = await db.fetch("SELECT * FROM creative WHERE org_id=$1", org_id)
    assert len(creatives) == 1
    c = creatives[0]
    assert c["platform_creative_id"] == "cr1"
    assert c["format"] == "image"
    assert c["headline"] == "Headline" and c["primary_text"] == "Primary text"
    assert c["cta_type"] == "SHOP_NOW" and c["link_url"] == "https://dest.example"


async def test_discover_and_sync_entities_is_idempotent(db, blobs, settings, org_id):
    await _seed_fivetran_schema(db)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)

    assert await db.fetchval("SELECT count(*) FROM ad_account WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM ad WHERE org_id=$1", org_id) == 1
    assert await db.fetchval("SELECT count(*) FROM creative WHERE org_id=$1", org_id) == 1
```

(Check `tests/conftest.py` for an existing `org_id`/`blobs` fixture before assuming these names — `tests/ingest/test_repo.py` and other ingest tests already depend on ad_account fixtures, so a compatible fixture likely already exists; reuse it rather than inventing a new one. `JobContext(job=None)` is fine here since this task's transform function never reads `ctx.job` — only Task 9's job handler wrapper does.)

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/ingest/test_fivetran_transform.py -v`
Expected: FAIL — `cs.ingest.fivetran_transform` doesn't exist yet.

- [ ] **Step 4: Implement the transform**

Create `src/cs/ingest/fivetran_transform.py`:

```python
from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import queue
from cs.ingest import repo
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.ingest")


def _q(schema: str, table: str) -> str:
    # schema is always schema_for(org_id)'s output -- [a-z0-9_]+ only -- so this is not an
    # injection risk; it's still double-quoted because a schema starting with a digit
    # (org_id is a UUID) is not a bare-word-safe Postgres identifier.
    return f'"{schema}".{table}'


def _carousel_urls(raw: str | None) -> list[str] | None:
    """object_story_link_data_child_attachments -> picture URLs, same shape parse.py's
    _carousel_card_urls expected from the live Graph API's link_data.child_attachments."""
    if not raw:
        return None
    try:
        cards = json.loads(raw)
    except (TypeError, ValueError):
        return None
    urls = [c["picture"] for c in cards if isinstance(c, dict) and c.get("picture")]
    return urls or None


def _format_and_urls(row: asyncpg.Record) -> tuple[str, str | None, list[str] | None]:
    if row["video_id"]:
        return "video", None, None
    child_urls = _carousel_urls(row["object_story_link_data_child_attachments"])
    if child_urls:
        return "carousel", None, child_urls
    return "image", row["image_url"] or row["thumbnail_url"], None


async def _discover_accounts(
    db, org_id: UUID, connection_id: UUID, schema: str
) -> dict[str, UUID]:
    """Every distinct account_id seen in campaign_history becomes (or already is) an
    ad_account row, starting 'paused' until a human activates it -- same default the old
    OAuth callback used."""
    rows = await db.fetch(f"SELECT DISTINCT account_id FROM {_q(schema, 'campaign_history')}")
    by_platform_id: dict[str, UUID] = {}
    for r in rows:
        pid = r["account_id"]
        aid = await repo.upsert_ad_account(db, org_id, pid, pid, "USD", connection_id)
        by_platform_id[pid] = aid
    return by_platform_id


async def _sync_ads_and_creatives(
    db, org_id: UUID, schema: str, accounts_by_platform_id: dict[str, UUID]
) -> dict[str, int]:
    creative_rows = await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, 'creative_history')}
        ) t WHERE rn = 1
    """)
    creatives_by_platform_id: dict[str, asyncpg.Record] = {r["id"]: r for r in creative_rows}

    ad_rows = await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, 'ad_history')}
        ) t WHERE rn = 1
    """)

    new_creatives = 0
    synced_ads = 0
    for ad_row in ad_rows:
        acct_id = accounts_by_platform_id.get(ad_row["account_id"])
        if acct_id is None:
            continue  # a stale ad for an account not (yet) in campaign_history
        cr = creatives_by_platform_id.get(ad_row["creative_id"])
        if cr is None:
            continue  # ad references a creative Fivetran hasn't landed yet
        fmt, asset_url, child_urls = _format_and_urls(cr)
        platform_creative = PlatformCreative(
            platform_creative_id=cr["id"],
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
            platform_ad_id=ad_row["id"],
            name=ad_row["name"] or "",
            status=ad_row["status"] or "",
            adset_id=ad_row["ad_set_id"],
            campaign_id=ad_row["campaign_id"],
            platform_creative_id=cr["id"],
        )
        await repo.upsert_ad(db, org_id, acct_id, platform_ad, creative_id)
        synced_ads += 1

        if created:
            kind = "original_video" if fmt == "video" else "original_image"
            if fmt == "carousel" and child_urls:
                if not await repo.creative_has_primary_asset(db, org_id, creative_id):
                    await queue.enqueue(
                        db, org_id, "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": child_urls},
                        dedup_key=str(creative_id),
                    )
            elif asset_url and not await repo.creative_has_asset(db, org_id, creative_id, kind):
                await queue.enqueue(
                    db, org_id, "meta.download_creative",
                    {"creative_id": str(creative_id), "asset_url": asset_url, "kind": kind},
                    dedup_key=str(creative_id),
                )
            elif fmt == "video" and cr.get("video_id") is not None:
                # video_id alone doesn't give us a downloadable URL from these tables --
                # ad_video_history carries the source URL. Left for the insights/video
                # follow-up task rather than blocking entity sync on it.
                pass

    return {"new_creatives": new_creatives, "synced_ads": synced_ads}


async def discover_and_sync_entities(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    accounts_by_platform_id = await _discover_accounts(ctx.db, org_id, connection["id"], schema)
    counts = await _sync_ads_and_creatives(ctx.db, org_id, schema, accounts_by_platform_id)
    return {"accounts_discovered": len(accounts_by_platform_id), **counts}
```

Note: `upsert_creative` takes a `raw_path` (a blob key pointing at the raw API response, used by the old Graph-API flow to store the exact page it parsed). Fivetran's landed row *is* the durable raw copy already (versioned via `_fivetran_synced`), so there's nothing to mirror to blob storage here — `raw_path=""` is intentional, not a placeholder; `creative.raw_path` becomes vestigial for Fivetran-sourced rows. (If `upsert_creative`'s `raw_path` column has a `NOT NULL` constraint without a default, `""` satisfies it; confirm against `db/migrations/20260904000003_ingest.sql`'s `creative` table definition, which declares `raw_path TEXT NOT NULL` with no default — `""` is a valid non-null value.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/ingest/test_fivetran_transform.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/ingest/fivetran_transform.py tests/ingest/test_fivetran_transform.py
git commit -m "feat: fold Fivetran-landed campaign/ad/creative history into ad_account/ad/creative"
```

---

### Task 7: Insights transform (custom report -> `metric_daily`)

**Files:**
- Create: `src/cs/ingest/fivetran_insights.py`
- Test: `tests/ingest/test_fivetran_insights.py`

**Interfaces:**
- Consumes: `MetricRow` (`src/cs/connectors/base.py`), `repo.upsert_metrics` (unchanged).
- Produces: `sync_insights(ctx, org_id, connection) -> dict` (summary counts). Task 9's `meta.sync_from_fivetran` handler calls this.

**This task carries the risk flagged in the spec.** Fivetran's default `basic_ad` report only has `impressions`, `inline_link_clicks`, `spend`, `reach`, `frequency` — no `publisher_platform`/`platform_position` breakdown, no video metrics. Matching today's `INSIGHT_FIELDS` (`src/cs/connectors/meta/connector.py`, being deleted in Task 11 — read it now before it's gone) requires a **Fivetran custom report** configured with `breakdowns: ["publisher_platform", "platform_position"]` and fields including `clicks`, `actions`, `action_values`, `video_play_actions`, `video_thruplay_watched_actions`, `video_p50_watched_actions`, `video_p100_watched_actions`. Fivetran's established pattern (seen in the prebuilt `basic_ad` -> `basic_ad_actions`/`basic_ad_action_values` child tables) is to unpivot each array-valued field into its own child table keyed by `(ad_id, date, action_type)` (plus `index`). This task assumes a custom report named `ad_insights` lands as:

- `ad_insights` — one row per `(ad_id, date, publisher_platform, platform_position)`: `ad_id, date, publisher_platform, platform_position, spend, impressions, clicks, inline_link_clicks`
- `ad_insights_actions` — `(ad_id, date, action_type, index, value)`
- `ad_insights_action_values` — same shape, monetary value
- `ad_insights_video_play_actions`, `ad_insights_video_thruplay_watched_actions`, `ad_insights_video_p50_watched_actions`, `ad_insights_video_p100_watched_actions` — each `(ad_id, date, action_type, index, value)`

- [ ] **Step 1: Configure and verify the custom report against a live connector**

Using the connector created in Task 6 Step 1 (or a new one), configure a custom report through the Fivetran dashboard (Connector -> Custom Reports) or the `POST /connectors/{id}/schema/{schema}/tables/{table}/custom_reports`-style API named `ad_insights`, `report_type: "AD"`, `dimensions: ["ad_id", "date"]`, `breakdowns: ["publisher_platform", "platform_position"]`, `fields: ["spend", "impressions", "clicks", "inline_link_clicks", "actions", "action_values", "video_play_actions", "video_thruplay_watched_actions", "video_p50_watched_actions", "video_p100_watched_actions"]`. After it syncs, run `\dt "<schema>".ad_insights*` against the destination and compare the actual table/column names to the assumption above. Adjust every table/column reference in Step 3 below to match — this is the single most important verification step in this plan; do not skip it before trusting this task's SQL in production. The fixture test in Step 2 will pass regardless of what you find, since it defines its own schema — it only proves the fold logic is correct given *some* consistent shape, not that the assumed shape is Fivetran's real one.

- [ ] **Step 2: Write the failing test**

Create `tests/ingest/test_fivetran_insights.py`:

```python
from datetime import date
from uuid import UUID

from cs.ingest import fivetran_insights, repo
from cs.jobs.registry import JobContext
from cs.connectors.base import PlatformAd, PlatformCreative

SCHEMA = "testorg__meta_ads"


async def _seed(db, org_id):
    await db.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{SCHEMA}"')
    await db.execute(f'CREATE TABLE "{SCHEMA}".ad_insights (ad_id TEXT, date DATE, '
                      f'publisher_platform TEXT, platform_position TEXT, spend NUMERIC, '
                      f'impressions INT, clicks INT, inline_link_clicks INT)')
    for t in (
        "ad_insights_actions", "ad_insights_action_values",
        "ad_insights_video_play_actions", "ad_insights_video_thruplay_watched_actions",
        "ad_insights_video_p50_watched_actions", "ad_insights_video_p100_watched_actions",
    ):
        await db.execute(f'CREATE TABLE "{SCHEMA}".{t} '
                          f'(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)')

    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights VALUES
        ('ad1', '2026-09-01', 'facebook', 'feed', 10.0, 1000, 20, 20)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_actions VALUES ('ad1', '2026-09-01', 'omni_purchase', 0, 2)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_action_values VALUES
        ('ad1', '2026-09-01', 'omni_purchase', 0, 100)
    """)
    await db.execute(f"""
        INSERT INTO "{SCHEMA}".ad_insights_video_thruplay_watched_actions VALUES
        ('ad1', '2026-09-01', 'video_view', 0, 5)
    """)

    acct = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
    )
    await repo.upsert_ad(db, org_id, acct, PlatformAd("ad1", "A", "ACTIVE", None, None, "cr1"), cid)
    return acct


async def test_sync_insights_folds_action_breakdowns_into_metric_daily(db, blobs, settings, org_id):
    acct = await _seed(db, org_id)
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)

    summary = await fivetran_insights.sync_insights(ctx, org_id, connection)
    assert summary["written"] == 1 and summary["skipped"] == 0

    rows = await db.fetch("SELECT * FROM metric_daily WHERE org_id=$1", org_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["ad_account_id"] == acct
    assert row["placement"] == "facebook:feed"
    assert row["spend"] == 10 and row["impressions"] == 1000 and row["clicks"] == 20
    assert row["conversions"] == 2 and row["conversion_value"] == 100
    assert row["video_thruplay"] == 5 and row["video_3s_views"] == 0
```

(`org_id`/`blobs`/`settings`/`db` fixtures per the convention already used in `tests/ingest/test_fivetran_transform.py`.)

- [ ] **Step 3: Run it to verify it fails**

Run: `pytest tests/ingest/test_fivetran_insights.py -v`
Expected: FAIL — `cs.ingest.fivetran_insights` doesn't exist yet.

- [ ] **Step 4: Implement the transform**

Create `src/cs/ingest/fivetran_insights.py`:

```python
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import MetricRow
from cs.ingest import repo
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


def _q(schema: str, table: str) -> str:
    return f'"{schema}".{table}'


async def _action_totals(
    db, schema: str, table: str
) -> dict[tuple[str, date], dict[str, Decimal]]:
    """(ad_id, date) -> {action_type: value}, summed across `index` (Meta can report the
    same action_type more than once per breakdown row)."""
    rows = await db.fetch(f"""
        SELECT ad_id, date, action_type, sum(value) AS total
        FROM {_q(schema, table)} GROUP BY ad_id, date, action_type
    """)
    out: dict[tuple[str, date], dict[str, Decimal]] = {}
    for r in rows:
        out.setdefault((r["ad_id"], r["date"]), {})[r["action_type"]] = Decimal(str(r["total"]))
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


async def sync_insights(ctx: JobContext, org_id: UUID, connection: asyncpg.Record) -> dict[str, Any]:
    schema = connection["schema_name"]
    db = ctx.db

    actions = await _action_totals(db, schema, "ad_insights_actions")
    action_values = await _action_totals(db, schema, "ad_insights_action_values")
    video_3s = await _action_totals(db, schema, "ad_insights_video_play_actions")
    video_thruplay = await _action_totals(db, schema, "ad_insights_video_thruplay_watched_actions")
    video_p50 = await _action_totals(db, schema, "ad_insights_video_p50_watched_actions")
    video_p100 = await _action_totals(db, schema, "ad_insights_video_p100_watched_actions")

    base_rows = await db.fetch(f"SELECT * FROM {_q(schema, 'ad_insights')}")
    metric_rows: list[MetricRow] = []
    for r in base_rows:
        key = (r["ad_id"], r["date"])
        metric_rows.append(
            MetricRow(
                platform_ad_id=r["ad_id"],
                day=r["date"],
                placement=f"{r['publisher_platform'] or 'unknown'}:{r['platform_position'] or 'unknown'}",
                spend=Decimal(str(r["spend"] or 0)),
                impressions=int(r["impressions"] or 0),
                clicks=int(r["clicks"] or 0),
                link_clicks=int(r["inline_link_clicks"] or 0),
                conversions=_first_matching(actions.get(key), CONVERSION_ACTION_TYPES),
                conversion_value=_first_matching(action_values.get(key), CONVERSION_ACTION_TYPES),
                video_3s_views=_video_total(video_3s.get(key)),
                video_thruplay=_video_total(video_thruplay.get(key)),
                video_p50=_video_total(video_p50.get(key)),
                video_p100=_video_total(video_p100.get(key)),
                raw=dict(r),
            )
        )

    # metric_daily is keyed per ad_account_id, but ad_insights doesn't carry one --
    # upsert_metrics resolves platform_ad_id -> (ad_account_id, creative_id) via the `ad`
    # table itself, so every ad in this org's insights is grouped by its actual account.
    by_account: dict[UUID, list[MetricRow]] = {}
    ad_rows = await db.fetch("SELECT id, ad_account_id, platform_ad_id FROM ad WHERE org_id=$1", org_id)
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

- [ ] **Step 5: Run the test to verify it passes**

Run: `pytest tests/ingest/test_fivetran_insights.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/ingest/fivetran_insights.py tests/ingest/test_fivetran_insights.py
git commit -m "feat: fold Fivetran custom-report insights into metric_daily"
```

---

### Task 8: Rewrite asset mirroring without a connector object

**Files:**
- Modify: `src/cs/ingest/service.py` (replace entirely)
- Test: `tests/ingest/test_service.py` (new — no such file exists today; the old coverage lived in `tests/ingest/test_jobs.py` via a `FakeConnector`)

**Interfaces:**
- Consumes: nothing from the connector layer (that's the point of this task).
- Produces: `download_creative(ctx, http, creative_id, asset_url, kind) -> None`, `download_carousel(ctx, http, creative_id, asset_urls) -> None` — same behavior as before, minus the `AdPlatformConnector` indirection.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_service.py`:

```python
import httpx
import respx

from cs.ingest import repo, service
from cs.connectors.base import PlatformCreative
from cs.jobs.registry import JobContext


async def _creative(db, org_id):
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db, org_id, acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
    )
    return cid


@respx.mock
async def test_download_creative_stores_asset_and_queues_tagging(db, blobs, settings, org_id):
    respx.get("https://cdn.example/img.jpg").mock(
        return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )
    cid = await _creative(db, org_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        await service.download_creative(ctx, http, cid, "https://cdn.example/img.jpg", "original_image")
    assert await repo.creative_has_primary_asset(db, org_id, cid)
    assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"


@respx.mock
async def test_download_carousel_stores_each_card_in_order(db, blobs, settings, org_id):
    respx.get("https://cdn.example/0.jpg").mock(
        return_value=httpx.Response(200, content=b"a", headers={"content-type": "image/jpeg"})
    )
    respx.get("https://cdn.example/1.jpg").mock(
        return_value=httpx.Response(200, content=b"b", headers={"content-type": "image/jpeg"})
    )
    cid = await _creative(db, org_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        await service.download_carousel(
            ctx, http, cid, ["https://cdn.example/0.jpg", "https://cdn.example/1.jpg"]
        )
    cards = await repo.list_assets_by_kind(db, org_id, cid, "original_image")
    assert [c["seq"] for c in cards] == [0, 1]
    assert await repo.creative_has_primary_asset(db, org_id, cid)
```

(Match `org_id`/`blobs` fixtures to whatever `tests/ingest/test_fivetran_transform.py` used in Task 6.)

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/ingest/test_service.py -v`
Expected: FAIL — `service.download_creative` still requires a `connector` argument.

- [ ] **Step 3: Rewrite `service.py`**

Replace `src/cs/ingest/service.py` entirely with:

```python
from __future__ import annotations

from uuid import UUID

import httpx

from cs.core import queue
from cs.core.blobs import sha256_hex
from cs.ingest import repo
from cs.jobs.registry import JobContext

_EXT_BY_MIME = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}


async def _fetch(http: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    resp = await http.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type", "application/octet-stream").split(";")[0]


async def download_creative(
    ctx: JobContext, http: httpx.AsyncClient, creative_id: UUID, asset_url: str, kind: str
) -> None:
    org_id = ctx.job.org_id
    data, mime = await _fetch(http, asset_url)
    ext = _EXT_BY_MIME.get(mime, "bin")
    key = f"{org_id}/creatives/{creative_id}/original.{ext}"
    await ctx.blobs.put(key, data, mime)
    asset_id = await repo.insert_asset(ctx.db, org_id, creative_id, kind, key, sha256_hex(data), mime)
    await repo.set_primary_asset(ctx.db, org_id, creative_id, asset_id)
    await queue.enqueue(
        ctx.db, org_id, "tagging.tag_creative", {"creative_id": str(creative_id)},
        dedup_key=f"tag:{creative_id}",
    )


async def download_carousel(
    ctx: JobContext, http: httpx.AsyncClient, creative_id: UUID, asset_urls: list[str]
) -> None:
    org_id = ctx.job.org_id
    primary_id = None
    for i, url in enumerate(asset_urls):
        existing = await repo.get_asset_by_seq(ctx.db, org_id, creative_id, "original_image", i)
        if existing is not None:
            asset_id = existing["id"]
        else:
            data, mime = await _fetch(http, url)
            ext = {"image/png": "png", "image/jpeg": "jpg"}.get(mime, "jpg")
            key = f"{org_id}/creatives/{creative_id}/card_{i:02d}.{ext}"
            await ctx.blobs.put(key, data, mime)
            asset_id = await repo.insert_asset(
                ctx.db, org_id, creative_id, "original_image", key, sha256_hex(data), mime, seq=i
            )
        if i == 0:
            primary_id = asset_id
    if primary_id is not None:
        await repo.set_primary_asset(ctx.db, org_id, creative_id, primary_id)
        await queue.enqueue(
            ctx.db, org_id, "tagging.tag_creative", {"creative_id": str(creative_id)},
            dedup_key=f"tag:{creative_id}",
        )
```

Everything from the old file is gone: `BACKFILL_DAYS`/`INCREMENTAL_DAYS`/`window()` (Fivetran owns historical depth now), `default_connector_factory`/`CONNECTOR_FACTORY` (no more connector abstraction), `_raw_sink`/`strip_access_tokens` usage (Fivetran's landed row is the durable raw copy), `sync_account` (replaced by `fivetran_transform.discover_and_sync_entities` + `fivetran_insights.sync_insights`), and the `CS_FAKE_CONNECTOR` block (Task 11 replaces it with fixture-seeded schema tables).

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/ingest/test_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/ingest/service.py tests/ingest/test_service.py
git commit -m "feat: rewrite asset mirroring without a connector object"
```

---

### Task 9: Wire up the job handlers

**Files:**
- Modify: `src/cs/ingest/jobs.py`
- Modify (rewrite the two tests that assumed the old handlers): `tests/ingest/test_jobs.py`

**Interfaces:**
- Consumes: `fivetran_client.get_connector` (Task 1), `fivetran_transform.discover_and_sync_entities` (Task 6), `fivetran_insights.sync_insights` (Task 7), `service.download_creative`/`download_carousel` (Task 8), `repo.set_meta_connection_status`/`get_meta_connection_by_id`/`list_ready_meta_connections_with_active_account` (Task 2).
- Produces: registered handlers `meta.await_first_sync`, `meta.sync_from_fivetran`, `meta.download_creative`, `meta.download_carousel`; schedule `ingest.nightly_sync`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/ingest/test_jobs.py`'s handler tests (leave any test in that file unrelated to `meta.backfill`/`meta.sync_incremental`/the `FakeConnector` untouched — check the file first; if it turns out every test in it depends on the removed handlers, replace the whole file) with:

```python
import httpx
import respx

from cs.core import queue
from cs.ingest import jobs, repo
from cs.jobs.registry import JobContext
from cs.jobs.worker import run_once

F = "https://api.fivetran.com/v1"


@respx.mock
async def test_await_first_sync_reraises_until_fivetran_finishes(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1", "status": {"setup_state": "connected"}}})
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)}, max_attempts=3)
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "pending"
    assert await db.fetchval("SELECT status FROM job") == "pending"  # retried, not dead yet


@respx.mock
async def test_await_first_sync_enqueues_sync_once_fivetran_succeeds(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"id": "conn_1", "status": {"setup_state": "connected"}, "succeeded_at": "2026-09-01T00:00:00Z"}},
        )
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "ready"
    assert await db.fetchval("SELECT job_type FROM job WHERE status='pending'") == "meta.sync_from_fivetran"


@respx.mock
async def test_await_first_sync_marks_broken_connector_as_error(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1", "status": {"setup_state": "broken"}}})
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "error" and "reconnect" in row["last_error"].lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/ingest/test_jobs.py -v`
Expected: FAIL — `meta.await_first_sync` isn't registered yet.

- [ ] **Step 3: Rewrite `jobs.py`**

Replace `src/cs/ingest/jobs.py` entirely with:

```python
from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core import queue
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import fivetran_insights, fivetran_transform, repo, service
from cs.jobs.registry import JobContext, handler
from cs.jobs.scheduler import register_schedule

log = logging.getLogger("cs.ingest")


@handler("meta.await_first_sync")
async def await_first_sync(ctx: JobContext) -> None:
    conn_id = UUID(ctx.job.payload["meta_connection_id"])
    conn = await repo.get_meta_connection_by_id(ctx.db, conn_id)
    if conn is None:
        return  # connection was replaced by a newer connect attempt
    async with httpx.AsyncClient(timeout=30) as http:
        detail = await fivetran_client.get_connector(ctx.settings, http, conn["fivetran_connector_id"])
    status = detail.get("status") or {}
    if status.get("setup_state") == "broken":
        await repo.set_meta_connection_status(
            ctx.db, conn_id, "error", last_error="Fivetran setup is broken -- reconnect from Settings."
        )
        return
    if detail.get("succeeded_at"):
        await repo.set_meta_connection_status(ctx.db, conn_id, "ready")
        await queue.enqueue(
            ctx.db, ctx.job.org_id, "meta.sync_from_fivetran",
            {"meta_connection_id": str(conn_id)}, dedup_key=str(conn_id),
        )
        return
    if ctx.job.attempts >= ctx.job.max_attempts:
        await repo.set_meta_connection_status(
            ctx.db, conn_id, "error",
            last_error="Fivetran's first sync did not finish in time -- check the connector in the Fivetran dashboard.",
        )
        return
    raise RuntimeError("Fivetran has not completed its first sync yet")


@handler("meta.sync_from_fivetran")
async def sync_from_fivetran(ctx: JobContext) -> None:
    conn_id = UUID(ctx.job.payload["meta_connection_id"])
    conn = await repo.get_meta_connection_by_id(ctx.db, conn_id)
    if conn is None:
        return
    try:
        entities = await fivetran_transform.discover_and_sync_entities(ctx, ctx.job.org_id, conn)
        metrics = await fivetran_insights.sync_insights(ctx, ctx.job.org_id, conn)
    except Exception as exc:
        await repo.set_meta_connection_status(ctx.db, conn_id, "error", last_error=str(exc)[:500])
        raise
    await repo.set_meta_connection_status(ctx.db, conn_id, "ready", last_synced_at=datetime.now(UTC))
    log.info("fivetran sync complete", extra={**entities, **metrics, "org_id": str(ctx.job.org_id)})


@handler("meta.download_creative")
async def download_creative(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await service.download_creative(ctx, http, creative_id, p["asset_url"], p["kind"])
    log.info("creative downloaded", extra={"creative_id": str(creative_id)})


@handler("meta.download_carousel")
async def download_carousel(ctx: JobContext) -> None:
    p = ctx.job.payload
    creative_id = UUID(p["creative_id"])
    async with httpx.AsyncClient(timeout=120) as http:
        await service.download_carousel(ctx, http, creative_id, p["asset_urls"])
    log.info("carousel downloaded", extra={"creative_id": str(creative_id), "cards": len(p["asset_urls"])})


@register_schedule("ingest.nightly_sync", every_seconds=86400)
async def nightly_sync(db: Database, settings: Settings) -> None:
    for c in await repo.list_ready_meta_connections_with_active_account(db):
        await queue.enqueue(
            db, c["org_id"], "meta.sync_from_fivetran",
            {"meta_connection_id": str(c["id"])}, dedup_key=str(c["id"]),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/ingest/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/ingest/jobs.py tests/ingest/test_jobs.py
git commit -m "feat: wire meta.await_first_sync / meta.sync_from_fivetran job handlers"
```

---

### Task 10: Remove the dead Meta OAuth/connector code

**Files:**
- Delete: `src/cs/connectors/meta/client.py`, `src/cs/connectors/meta/connector.py`, `src/cs/connectors/meta/parse.py`, `src/cs/connectors/meta/scrub.py`, `src/cs/connectors/meta/__init__.py`, `src/cs/connectors/fakes.py`, `src/cs/core/crypto.py`, `tests/connectors/test_meta_client.py`, `tests/connectors/test_meta_parse.py`, `tests/connectors/test_fake_connector.py`, `tests/core/test_crypto.py`, `tests/fixtures/meta/` (whole directory)
- Modify: `src/cs/connectors/base.py`, `src/cs/core/config.py`, `.env.example`

**Interfaces:**
- Consumes: nothing (this is pure removal).
- Produces: `src/cs/connectors/base.py` retains only `PlatformAd`, `PlatformCreative`, `MetricRow` (the `AdPlatformConnector` Protocol and `Format` type alias it used are gone — `Format` moves inline as a `Literal` on `PlatformCreative.format`'s type if it's still referenced only there, otherwise keep it as a standalone alias since `PlatformCreative` still uses it).

- [ ] **Step 1: Delete the connector module and its tests**

```bash
git rm -r src/cs/connectors/meta src/cs/connectors/fakes.py src/cs/core/crypto.py \
  tests/connectors/test_meta_client.py tests/connectors/test_meta_parse.py \
  tests/connectors/test_fake_connector.py tests/core/test_crypto.py tests/fixtures/meta
```

- [ ] **Step 2: Strip `base.py` down to the persisted dataclasses**

Replace `src/cs/connectors/base.py` entirely with:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

Format = Literal["image", "video", "carousel"]


@dataclass
class PlatformAd:
    platform_ad_id: str
    name: str
    status: str
    adset_id: str | None
    campaign_id: str | None
    platform_creative_id: str


@dataclass
class PlatformCreative:
    platform_creative_id: str
    format: Format
    headline: str | None
    primary_text: str | None
    description: str | None
    cta_type: str | None
    link_url: str | None
    asset_url: str | None
    asset_mime: str | None
    raw: dict = field(default_factory=dict)
    child_asset_urls: list[str] | None = None


@dataclass
class MetricRow:
    platform_ad_id: str
    day: date
    placement: str
    spend: Decimal
    impressions: int
    clicks: int
    link_clicks: int
    conversions: Decimal
    conversion_value: Decimal
    video_3s_views: int
    video_thruplay: int
    video_p50: int
    video_p100: int
    raw: dict = field(default_factory=dict)
```

(Identical to today's file minus the `Protocol` import and the `AdPlatformConnector` class.)

- [ ] **Step 3: Remove the dead Settings fields**

In `src/cs/core/config.py`, remove `meta_app_id`, `meta_app_secret`, `meta_redirect_uri`, `meta_api_version`, and `token_encryption_key`.

In `.env.example`, remove the `TOKEN_ENCRYPTION_KEY` line and its generator comment, and the `META_APP_ID`/`META_APP_SECRET`/`META_REDIRECT_URI`/`META_API_VERSION` lines. Add in their place:

```
FIVETRAN_API_KEY=
FIVETRAN_API_SECRET=
FIVETRAN_GROUP_ID=
WEB_BASE_URL=http://localhost:3000
```

- [ ] **Step 4: Run the full backend test suite**

Run: `pytest tests/ -v`
Expected: PASS

Run: `grep -rn "cs\.connectors\.meta\|AdPlatformConnector\|FakeConnector\|token_encryption_key\|meta_app_id\|meta_app_secret\|meta_redirect_uri\|meta_api_version\|cs\.core\.crypto" src tests`
Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: remove dead Meta OAuth/Graph-API connector code"
```

---

### Task 11: Demo/e2e without a fake connector

**Files:**
- Delete: `scripts/seed_dev_account.py`
- Create: `src/cs/ingest/fivetran_fixtures.py`, `scripts/seed_dev_fivetran.py`
- Modify: `Makefile`, `README.md`, `src/cs/tagging/jobs.py` (drop its `CS_FAKE_CONNECTOR` branch, if applicable), `web/tests/e2e/onboarding-to-review.spec.ts`, `tests/e2e/test_loop.py`

**Interfaces:**
- Consumes: `fivetran_transform.discover_and_sync_entities`, `fivetran_insights.sync_insights` (Tasks 6-7) — the demo/e2e path now exercises the *real* transform against fixture-seeded schema tables, not a separate fake code path.
- Produces: a `meta_connection` + a fixture `<schema>__meta_ads` schema for the dev org, so `meta.sync_from_fivetran` run against it behaves exactly like a real (tiny) Fivetran sync.

- [ ] **Step 1: Check `src/cs/tagging/jobs.py`'s `CS_FAKE_CONNECTOR` branch**

Read the block at `src/cs/tagging/jobs.py:236` (`if os.environ.get("CS_FAKE_CONNECTOR"):`) in full before touching it — confirm exactly what it fakes (likely the tagging model call, not the connector, given its location in `tagging/jobs.py` rather than `ingest/`) so Step 4 below removes only what's actually about the deleted `FakeConnector`/ingest path, not an unrelated tagging fake that should stay for the demo loop to remain cheap to run.

- [ ] **Step 2: Factor the fixture-schema SQL into a reusable module**

Both the seed script and `tests/e2e/test_loop.py` need the same Fivetran-shaped tables, with different rows in them. Rather than duplicate the DDL, extract it into `src/cs/ingest/fivetran_fixtures.py`, alongside the other test/dev fakes this codebase already keeps under `src/cs/*/fakes.py` (e.g. `cs.generation.fakes`, `cs.tagging.fakes`):

```python
"""Fivetran-shaped fixture schema for local demo/e2e runs -- no live Fivetran connector or
Meta credentials needed. create_fixture_schema builds the tables fivetran_transform.py /
fivetran_insights.py read; callers insert their own rows into them."""

from __future__ import annotations

from cs.core.db import Database


async def create_fixture_schema(db: Database, schema: str) -> None:
    await db.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await db.execute(f'CREATE SCHEMA "{schema}"')
    await db.execute(f"""
        CREATE TABLE "{schema}".campaign_history (
          id TEXT, account_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_set_history (
          id TEXT, account_id TEXT, campaign_id TEXT, name TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".ad_history (
          id TEXT, account_id TEXT, ad_set_id TEXT, campaign_id TEXT, creative_id TEXT,
          name TEXT, status TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f"""
        CREATE TABLE "{schema}".creative_history (
          id TEXT, account_id TEXT, name TEXT, title TEXT, body TEXT,
          call_to_action_type TEXT, image_url TEXT, thumbnail_url TEXT,
          object_story_link_data_link TEXT, video_id TEXT,
          object_story_link_data_child_attachments TEXT, _fivetran_synced TIMESTAMPTZ
        )
    """)
    await db.execute(f'CREATE TABLE "{schema}".ad_insights (ad_id TEXT, date DATE, '
                      f'publisher_platform TEXT, platform_position TEXT, spend NUMERIC, '
                      f'impressions INT, clicks INT, inline_link_clicks INT)')
    for t in (
        "ad_insights_actions", "ad_insights_action_values",
        "ad_insights_video_play_actions", "ad_insights_video_thruplay_watched_actions",
        "ad_insights_video_p50_watched_actions", "ad_insights_video_p100_watched_actions",
    ):
        await db.execute(f'CREATE TABLE "{schema}".{t} '
                          f'(ad_id TEXT, date DATE, action_type TEXT, index INT, value NUMERIC)')


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
) -> None:
    await db.execute(
        f'INSERT INTO "{schema}".campaign_history VALUES ($1, $2, $3, now())',
        campaign_id, account_id, f"Campaign {name}",
    )
    await db.execute(
        f'INSERT INTO "{schema}".ad_history VALUES ($1, $2, $3, $4, $5, $6, $7, now())',
        ad_id, account_id, adset_id, campaign_id, creative_id, name, "ACTIVE",
    )
    await db.execute(
        f'INSERT INTO "{schema}".creative_history VALUES '
        f'($1, $2, $3, $4, $5, $6, $7, $8, $9, NULL, NULL, now())',
        creative_id, account_id, name, headline, "Primary text", "SHOP_NOW",
        image_url, None, "https://example.com",
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
        ad_id, day, placement[0], placement[1], spend, impressions, clicks, link_clicks,
    )
```

- [ ] **Step 3: Write the seed script**

Create `scripts/seed_dev_fivetran.py`, replacing `scripts/seed_dev_account.py`:

```python
"""Seed a fake Fivetran-landed schema plus a meta_connection for the dev org, so the e2e
flow can activate an account and exercise the real transform without a live Fivetran
connector or Meta credentials."""

import asyncio
import os

from cs.connectors.fivetran.connect import schema_for
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database
from cs.ingest.fivetran_fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_daily_insight,
)
from cs.ingest.fivetran_transform import discover_and_sync_entities
from cs.ingest.repo import upsert_meta_connection
from cs.jobs.registry import JobContext
from cs.orgs.repo import get_or_create_org


async def main() -> None:
    s = get_settings()
    db = await Database.connect(s.database_url)
    org = await get_or_create_org(db, os.environ.get("NEXT_PUBLIC_DEV_ORG", "org_dev"), "Dev org")
    schema = schema_for(org)

    await create_fixture_schema(db, schema)
    for name, headline in (("demo_a", "Headline demo_a"), ("demo_b", "Headline demo_b")):
        await insert_campaign_ad_creative(
            db, schema, campaign_id=f"c_{name}", account_id="1", adset_id=f"as_{name}",
            ad_id=f"ad_{name}", creative_id=name, name=name, headline=headline,
            image_url=f"https://placehold.co/600x600.png?text={name}",
        )
        await insert_daily_insight(
            db, schema, ad_id=f"ad_{name}", day="2026-09-01", spend=10, impressions=1000,
            clicks=20, link_clicks=20,
        )

    conn_id = await upsert_meta_connection(db, org, "dev_fake_connector", schema)
    await db.execute("UPDATE meta_connection SET sync_status='ready' WHERE id=$1", conn_id)
    connection = await db.fetchrow("SELECT * FROM meta_connection WHERE id=$1", conn_id)
    ctx = JobContext(db=db, blobs=blob_store_from_settings(s), settings=s, job=None)
    await discover_and_sync_entities(ctx, org, connection)

    await db.close()
    print("seeded")


if __name__ == "__main__":
    asyncio.run(main())
```

(Fixture data mirrors what `service.py`'s old `CS_FAKE_CONNECTOR` block registered: two creatives, `demo_a`/`demo_b`, each with a headline and an image URL a real HTTP client can fetch -- `placehold.co` serves a real PNG, so `meta.download_creative` mirrors something real instead of failing. Running `discover_and_sync_entities` once at seed time, rather than leaving discovery to the first real job, is what leaves a `paused` `ad_account` row ready for the onboarding page's "Activate" button -- exactly what `scripts/seed_dev_account.py` used to leave.)

Delete the old script:

```bash
git rm scripts/seed_dev_account.py
```

- [ ] **Step 4: Update the Makefile's `demo` target**

In `Makefile`, the `demo` target currently reads:

```makefile
demo:
	CS_FAKE_CONNECTOR=1 $(MAKE) -j3 api worker scheduler
```

Replace with:

```makefile
demo:
	AUTH_MODE=dev uv run python scripts/seed_dev_fivetran.py
	AUTH_MODE=dev $(MAKE) -j3 api worker scheduler
```

- [ ] **Step 5: Remove the `CS_FAKE_CONNECTOR` branch from `tagging/jobs.py` only if Step 1 found it's about the deleted connector**

If Step 1 confirmed the block at `src/cs/tagging/jobs.py:236` fakes the tagging/embedding model call (not the ingest connector), leave it entirely alone -- it's an unrelated fake this plan has no reason to touch. If it turns out to reference `cs.connectors.fakes` or `cs.ingest.service.CONNECTOR_FACTORY` directly, remove exactly that reference the same way Task 10 removed other `FakeConnector` call sites, and re-run `pytest tests/tagging/ -v` to confirm nothing else in that file depended on it.

- [ ] **Step 6: Update the Playwright e2e spec's header comment**

In `web/tests/e2e/onboarding-to-review.spec.ts`, update the header comment (no more `CS_FAKE_CONNECTOR`):

```ts
// Requires: `AUTH_MODE=dev make api` and `AUTH_MODE=dev make worker` running, and a fixture
// meta_connection + Fivetran-shaped schema for the dev org inserted by
// `scripts/seed_dev_fivetran.py`.
// NEXT_PUBLIC_DEV_ORG must match the seeded org.
```

The test body itself is unchanged -- `/accounts/{id}/activate` still exists (Task 4) and still enqueues a sync job; `scripts/seed_dev_fivetran.py` (Step 3) already runs the transform once so the onboarding page has a `paused` account ready to activate.

- [ ] **Step 7: Rewrite `tests/e2e/test_loop.py`'s ingest setup**

This plan assumes `docs/superpowers/plans/2026-09-06-drop-meta-publish.md` has already landed, which already replaced this file's `# 4. approve + publish` / `# 5. next sync closes the loop` tail with a plain approve-and-check-download-fields assertion (that plan's Task 2, Step 12). What's left for this plan is the *setup* -- `test_full_loop` currently opens with a `FakeConnector` built from `PlatformAd`/`PlatformCreative`/`MetricRow` dataclasses and a `CONNECTOR_FACTORY` monkeypatch, neither of which exist anymore (Task 8 deleted `CONNECTOR_FACTORY`; Task 10 deleted `FakeConnector`).

Replace the file's import block (currently `from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative`, `from cs.connectors.fakes import FakeConnector`, and `from cs.ingest import service as ingest_service`) with:

```python
from cs.connectors.fivetran.connect import schema_for
from cs.ingest import fivetran_insights, fivetran_transform
from cs.ingest import repo as ingest_repo
from cs.ingest.fivetran_fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_daily_insight,
)
from cs.jobs.registry import JobContext
```

Delete the `monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)` line and the `fake = FakeConnector()` line above it.

Replace the whole block from `days = [date.today() - timedelta(days=i + 1) for i in range(10)]` down through the end of the original `# 1. connect + activate (backfill)` section (i.e. everything up to and including `assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 12`) with:

```python
    # 1. seed a fixture Fivetran schema (no live connector/Meta creds needed), run the
    # transform directly to discover the account (mirroring what meta.sync_from_fivetran
    # does after a real Fivetran connector's first sync completes), then activate it.
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    schema = schema_for(org_id)
    await create_fixture_schema(db, schema)
    for i in range(12):
        name = f"q{i}" if i < 6 else f"s{i}"
        ctr_clicks = 30 if i < 6 else 10
        await insert_campaign_ad_creative(
            db, schema, campaign_id="c1", account_id="1", adset_id="as1",
            ad_id=f"ad_{name}", creative_id=name, name=name, headline=name,
            image_url=f"https://img/{name}",
        )
        for d in (date.today() - timedelta(days=n + 1) for n in range(10)):
            await insert_daily_insight(
                db, schema, ad_id=f"ad_{name}", day=d.isoformat(), spend=30,
                impressions=1000, clicks=ctr_clicks, link_clicks=ctr_clicks,
            )

    conn_id = await ingest_repo.upsert_meta_connection(db, org_id, "conn_e2e", schema)
    await db.execute("UPDATE meta_connection SET sync_status='ready' WHERE id=$1", conn_id)
    connection = await ingest_repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await fivetran_insights.sync_insights(ctx, org_id, connection)

    acct = (await ingest_repo.list_ad_accounts(db, org_id))[0]["id"]
    await client.post(f"/accounts/{acct}/activate", headers=H)
    await _drain(db, blobs, settings)
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 12
```

Note `acct` here replaces the old `acct = await ingest_repo.upsert_ad_account(...)` variable used later in the file. Per the publish-removal plan, the now-truncated `# 4.` section no longer references `acct` at all, so nothing downstream strictly depends on it -- it stays defined here in case a future test extends the file. The `from uuid import UUID` import already present mid-function in the original file (inline at the top of the old `# 1.` section) moves to the top-level import block alongside the new imports listed above, replacing its old inline placement.

- [ ] **Step 8: Run everything**

Run: `pytest tests/ -v && cd web && npx vitest run && npx tsc --noEmit`
Expected: PASS

Run (manual, not automated -- confirms the actual demo loop): `make demo` in one terminal, then `cd web && npx playwright test onboarding-to-review` in another.
Expected: the Playwright spec passes against the freshly seeded fixture data.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: replace CS_FAKE_CONNECTOR with fixture-seeded Fivetran schema for demo/e2e"
```
