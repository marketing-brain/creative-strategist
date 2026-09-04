# Phase 0: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the repo, config, Postgres with migrations, blob storage, a Postgres-backed job queue with worker and scheduler processes, Clerk-compatible auth, and a FastAPI skeleton, so every later phase has a tested base to build on.

**Architecture:** One Python package `cs` with `core` (config, ids, db, blobs, queue), `jobs` (handler registry, worker loop, scheduler loop), and `api` (FastAPI app, auth dependency). Three entry points run the same code: `cs.api`, `cs.worker`, `cs.scheduler`. Postgres is the only stateful dependency; MinIO stands in for S3 locally.

**Tech Stack:** Python 3.12, uv, FastAPI, uvicorn, asyncpg, pydantic-settings, PyJWT (with `cryptography`), boto3, dbmate, pytest, pytest-asyncio, httpx, ruff, docker compose (postgres `pgvector/pgvector:pg16`, `minio/minio`).

**Spec:** `docs/superpowers/specs/2026-09-04-creative-strategist-design.md` (sections 3, 4, 11, 13)

## Global Constraints

- Python `>=3.12`; Node `>=22` (used in Phase 4 and 5 only).
- Every table has `org_id UUID NOT NULL` except `org` itself; every repository function signature starts `(db, org_id, ...)`.
- IDs are UUIDv7 generated app-side; timestamps `timestamptz`; enums `TEXT` + `CHECK`.
- Migrations are raw SQL files under `db/migrations/` run by `dbmate`; no ORM.
- Blobs never enter Postgres. Store `path` + `sha256`.
- Job status transitions are one-way: `pending → running → done | failed → (pending on retry) | dead`.
- Tests run against a real Postgres database named in `DATABASE_URL_TEST`; each test starts with truncated tables.
- Default Claude model string, when referenced in config: `claude-opus-5`.

---

## File structure

```
creative-strategist/
├── pyproject.toml              # package metadata, deps, ruff + pytest config
├── Makefile                    # dev, migrate, test, lint targets
├── docker-compose.yml          # postgres (pgvector) + minio
├── .env.example                # every setting with a safe local default
├── .github/workflows/ci.yml
├── db/migrations/              # dbmate SQL files
│   └── 20260904000001_foundation.sql
├── src/cs/
│   ├── __init__.py
│   ├── core/
│   │   ├── config.py           # Settings (pydantic-settings) + get_settings()
│   │   ├── ids.py              # new_id() -> UUIDv7
│   │   ├── db.py               # Database wrapper over asyncpg pool
│   │   ├── blobs.py            # BlobStore protocol, LocalBlobStore, S3BlobStore, sha256
│   │   └── queue.py            # enqueue / claim / complete / fail / reclaim_stale
│   ├── jobs/
│   │   ├── registry.py         # @handler decorator, JobContext, HANDLERS
│   │   ├── worker.py           # run_worker() loop + `python -m cs.worker`
│   │   └── scheduler.py        # run_scheduler() loop + `python -m cs.scheduler`
│   ├── api/
│   │   ├── app.py              # create_app(); `python -m cs.api` runs uvicorn
│   │   ├── auth.py             # AuthContext, current_auth dependency (dev + clerk modes)
│   │   ├── deps.py             # get_db, get_blobs, get_settings for routers
│   │   └── routers/health.py   # /health, /me
│   ├── worker.py               # thin __main__ shim
│   ├── scheduler.py            # thin __main__ shim
│   └── orgs/repo.py            # get_or_create_org_by_clerk_id, ensure_membership
└── tests/
    ├── conftest.py             # settings, db pool, migrations, truncate, blobs fixtures
    ├── core/test_ids.py
    ├── core/test_db.py
    ├── core/test_blobs.py
    ├── core/test_queue.py
    ├── jobs/test_worker.py
    ├── jobs/test_scheduler.py
    └── api/test_health_and_auth.py
```

---

### Task 1: Repo scaffold and settings

**Files:**
- Create: `pyproject.toml`, `Makefile`, `docker-compose.yml`, `.env.example`, `.gitignore`
- Create: `src/cs/__init__.py`, `src/cs/core/__init__.py`, `src/cs/core/config.py`
- Test: `tests/core/test_config.py`

**Interfaces:**
- Produces: `cs.core.config.Settings` (pydantic-settings model) and `get_settings() -> Settings` (lru-cached). Fields listed in the implementation below are the canonical names every later phase reads.

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "creative-strategist"
version = "0.1.0"
description = "AI creative strategist for paid social"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "asyncpg>=0.29",
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "PyJWT[crypto]>=2.9",
  "boto3>=1.35",
  "httpx>=0.27",
  "cryptography>=43",
  "python-multipart>=0.0.9",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.3",
  "pytest-asyncio>=0.24",
  "ruff>=0.6",
  "respx>=0.21",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/cs"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

- [ ] **Step 2: Create Makefile, docker-compose.yml, .env.example, .gitignore**

`Makefile`:

```makefile
.PHONY: install up down migrate migrate-test test lint dev api worker scheduler

install:
	uv sync --extra dev

up:
	docker compose up -d

down:
	docker compose down

migrate:
	dbmate --url "$$DATABASE_URL" --migrations-dir db/migrations up

migrate-test:
	dbmate --url "$$DATABASE_URL_TEST" --migrations-dir db/migrations up

test: migrate-test
	uv run pytest -q

lint:
	uv run ruff check . && uv run ruff format --check .

api:
	uv run python -m cs.api

worker:
	uv run python -m cs.worker

scheduler:
	uv run python -m cs.scheduler
```

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: cs
      POSTGRES_PASSWORD: cs
      POSTGRES_DB: cs
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data", "./db/init:/docker-entrypoint-initdb.d"]
  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: minio12345
    ports: ["9000:9000", "9001:9001"]
    volumes: ["miniodata:/data"]
volumes:
  pgdata: {}
  miniodata: {}
```

`db/init/01_test_db.sql` (creates the test database on first boot):

```sql
CREATE DATABASE cs_test;
```

`.env.example`:

```bash
DATABASE_URL=postgres://cs:cs@localhost:5432/cs?sslmode=disable
DATABASE_URL_TEST=postgres://cs:cs@localhost:5432/cs_test?sslmode=disable
BLOB_BACKEND=local            # local | s3
BLOB_ROOT=./var/blobs
S3_BUCKET=cs-blobs
S3_ENDPOINT_URL=http://localhost:9000
AWS_ACCESS_KEY_ID=minio
AWS_SECRET_ACCESS_KEY=minio12345
AUTH_MODE=dev                 # dev | clerk
CLERK_JWKS_URL=
CLERK_ISSUER=
TOKEN_ENCRYPTION_KEY=          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
PUBLIC_BASE_URL=http://localhost:8000
META_APP_ID=
META_APP_SECRET=
META_REDIRECT_URI=http://localhost:8000/meta/oauth/callback
META_API_VERSION=v21.0
ANTHROPIC_API_KEY=
MODEL_TAGGING=claude-opus-5
MODEL_INSIGHTS=claude-opus-5
MODEL_COPILOT=claude-opus-5
MODEL_COPY=claude-opus-5
AI_GATEWAY_API_KEY=
AI_GATEWAY_BASE_URL=https://ai-gateway.vercel.sh
IMAGE_MODEL=bfl/flux-2-pro
VIDEO_MODEL=klingai/kling-v3.0-i2v
TRANSCRIPTION_MODEL=openai/whisper-1
EMBEDDING_MODEL=openai/text-embedding-3-small
```

`.gitignore`:

```
.venv/
__pycache__/
*.pyc
.env
var/
node_modules/
.next/
.pytest_cache/
.ruff_cache/
```

- [ ] **Step 3: Write the failing settings test**

`tests/core/test_config.py`:

```python
from cs.core.config import Settings


def test_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setenv("DATABASE_URL_TEST", "postgres://y")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "k")
    s = Settings()
    assert s.database_url == "postgres://x"
    assert s.blob_backend == "local"
    assert s.model_tagging == "claude-opus-5"
    assert s.meta_api_version == "v21.0"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv sync --extra dev && uv run pytest tests/core/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cs'`

- [ ] **Step 5: Implement Settings**

`src/cs/__init__.py` and `src/cs/core/__init__.py`: empty files.

`src/cs/core/config.py`:

```python
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str = ""

    blob_backend: Literal["local", "s3"] = "local"
    blob_root: str = "./var/blobs"
    s3_bucket: str = "cs-blobs"
    s3_endpoint_url: str | None = None

    auth_mode: Literal["dev", "clerk"] = "dev"
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""

    token_encryption_key: str
    public_base_url: str = "http://localhost:8000"

    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = "http://localhost:8000/meta/oauth/callback"
    meta_api_version: str = "v21.0"

    anthropic_api_key: str = ""
    model_tagging: str = "claude-opus-5"
    model_insights: str = "claude-opus-5"
    model_copilot: str = "claude-opus-5"
    model_copy: str = "claude-opus-5"

    ai_gateway_api_key: str = ""
    ai_gateway_base_url: str = "https://ai-gateway.vercel.sh"
    image_model: str = "bfl/flux-2-pro"
    video_model: str = "klingai/kling-v3.0-i2v"
    transcription_model: str = "openai/whisper-1"
    embedding_model: str = "openai/text-embedding-3-small"

    worker_poll_seconds: float = 1.0
    job_lease_seconds: int = 600


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/core/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml Makefile docker-compose.yml .env.example .gitignore db/init src/cs tests/core/test_config.py uv.lock
git commit -m "chore: scaffold project with settings, compose, and tooling"
```

---

### Task 2: UUIDv7 ids

**Files:**
- Create: `src/cs/core/ids.py`
- Test: `tests/core/test_ids.py`

**Interfaces:**
- Produces: `new_id() -> uuid.UUID` (version 7, time-ordered).

- [ ] **Step 1: Write the failing test**

`tests/core/test_ids.py`:

```python
import uuid

from cs.core.ids import new_id


def test_new_id_is_uuid7_and_monotonic():
    a = new_id()
    b = new_id()
    assert isinstance(a, uuid.UUID)
    assert a.version == 7
    assert str(a) < str(b)  # time-ordered within the same millisecond via counter
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/cs/core/ids.py`:

```python
import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def new_id() -> uuid.UUID:
    """UUIDv7: 48-bit unix ms, 12-bit sequence, 62 random bits. Monotonic per process."""
    global _last_ms, _seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            _seq += 1
            if _seq >= 0xFFF:
                ms += 1
                _seq = 0
        else:
            _seq = 0
        _last_ms = ms
        rand = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (ms << 80) | (0x7 << 76) | (_seq << 64) | (0b10 << 62) | rand
        return uuid.UUID(int=value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/core/test_ids.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cs/core/ids.py tests/core/test_ids.py
git commit -m "feat(core): add UUIDv7 id generator"
```

---

### Task 3: Database wrapper, first migration, test fixtures

**Files:**
- Create: `src/cs/core/db.py`, `db/migrations/20260904000001_foundation.sql`, `tests/conftest.py`
- Test: `tests/core/test_db.py`

**Interfaces:**
- Produces: `cs.core.db.Database` with `await Database.connect(url) -> Database`, `close()`, `fetch(sql, *args) -> list[Record]`, `fetchrow(sql, *args) -> Record | None`, `fetchval(sql, *args)`, `execute(sql, *args) -> str`, `transaction()` async context manager yielding a `Database` bound to one connection.
- Produces test fixtures: `settings`, `db` (function-scoped, tables truncated before each test), `org_id` (a fresh org row), `blobs` (LocalBlobStore in tmp dir).
- Produces tables `org`, `user_org`, `job`.

- [ ] **Step 1: Write the migration**

`db/migrations/20260904000001_foundation.sql`:

```sql
-- migrate:up
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE org (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL,
  clerk_org_id TEXT UNIQUE,
  active_brand_kit_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_org (
  user_id TEXT NOT NULL,
  org_id UUID NOT NULL REFERENCES org(id),
  role TEXT NOT NULL CHECK (role IN ('member', 'admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, org_id)
);

CREATE TABLE job (
  id UUID PRIMARY KEY,
  org_id UUID NOT NULL REFERENCES org(id),
  job_type TEXT NOT NULL,
  dedup_key TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed', 'dead')),
  attempts INT NOT NULL DEFAULT 0,
  max_attempts INT NOT NULL DEFAULT 5,
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  locked_by TEXT,
  locked_at TIMESTAMPTZ,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX job_pending_dedup_idx ON job (job_type, dedup_key)
  WHERE status IN ('pending', 'running') AND dedup_key IS NOT NULL;
CREATE INDEX job_claim_idx ON job (status, run_after) WHERE status = 'pending';

-- migrate:down
DROP TABLE job;
DROP TABLE user_org;
DROP TABLE org;
```

- [ ] **Step 2: Write the failing test**

`tests/core/test_db.py`:

```python
from cs.core.ids import new_id


async def test_roundtrip_and_transaction_rollback(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, $2)", oid, "acme")
    row = await db.fetchrow("SELECT name FROM org WHERE id = $1", oid)
    assert row["name"] == "acme"

    try:
        async with db.transaction() as tx:
            await tx.execute("UPDATE org SET name = 'changed' WHERE id = $1", oid)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert await db.fetchval("SELECT name FROM org WHERE id = $1", oid) == "acme"
```

- [ ] **Step 3: Write conftest**

`tests/conftest.py`:

```python
import os
import subprocess
from pathlib import Path

import pytest

from cs.core.blobs import LocalBlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings() -> Settings:
    os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXk=")
    os.environ.setdefault("DATABASE_URL", "postgres://cs:cs@localhost:5432/cs?sslmode=disable")
    os.environ.setdefault(
        "DATABASE_URL_TEST", "postgres://cs:cs@localhost:5432/cs_test?sslmode=disable"
    )
    s = Settings()
    subprocess.run(
        ["dbmate", "--url", s.database_url_test, "--migrations-dir", str(ROOT / "db/migrations"), "up"],
        check=True,
        capture_output=True,
    )
    return s


@pytest.fixture
async def db(settings):
    database = await Database.connect(settings.database_url_test)
    tables = await database.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'schema_migrations'"
    )
    names = ", ".join(f'"{t["tablename"]}"' for t in tables)
    if names:
        await database.execute(f"TRUNCATE {names} CASCADE")
    yield database
    await database.close()


@pytest.fixture
async def org_id(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'test org')", oid)
    return oid


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")
```

Note: `cs.core.blobs` does not exist until Task 4. For this task, temporarily comment out the `blobs` fixture and its import; Task 4 restores them.

- [ ] **Step 4: Run test to verify it fails**

Run: `docker compose up -d && uv run pytest tests/core/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: cs.core.db`

- [ ] **Step 5: Implement Database**

`src/cs/core/db.py`:

```python
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )
    await conn.set_type_codec(
        "json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


class Database:
    def __init__(self, pool: asyncpg.Pool | None, conn: asyncpg.Connection | None = None):
        self._pool = pool
        self._conn = conn

    @classmethod
    async def connect(cls, url: str) -> Database:
        pool = await asyncpg.create_pool(url, min_size=1, max_size=10, init=_init_conn)
        return cls(pool)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _target(self):
        return self._conn if self._conn is not None else self._pool

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        return await self._target().fetch(sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        return await self._target().fetchrow(sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self._target().fetchval(sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self._target().execute(sql, *args)

    @asynccontextmanager
    async def transaction(self):
        if self._conn is not None:
            async with self._conn.transaction():
                yield self
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield Database(None, conn)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/core/test_db.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/cs/core/db.py db/migrations tests/conftest.py tests/core/test_db.py
git commit -m "feat(core): database wrapper, foundation migration, test fixtures"
```

---

### Task 4: Blob store

**Files:**
- Create: `src/cs/core/blobs.py`
- Modify: `tests/conftest.py` (restore `blobs` fixture)
- Test: `tests/core/test_blobs.py`

**Interfaces:**
- Produces: `BlobStore` Protocol: `async put(key: str, data: bytes, content_type: str) -> str`, `async get(key: str) -> bytes`, `async exists(key: str) -> bool`, `async url_for(key: str, expires_seconds: int = 3600) -> str`.
- Produces: `LocalBlobStore(root: Path, public_base_url: str = "")`, `S3BlobStore(bucket, endpoint_url=None)`, `sha256_hex(data: bytes) -> str`, `blob_store_from_settings(settings) -> BlobStore`.

- [ ] **Step 1: Write the failing test**

`tests/core/test_blobs.py`:

```python
from cs.core.blobs import LocalBlobStore, sha256_hex


async def test_local_put_get_exists_url(tmp_path):
    store = LocalBlobStore(tmp_path, public_base_url="http://localhost:8000")
    key = "org1/creatives/abc/original.png"
    assert not await store.exists(key)
    await store.put(key, b"hello", "image/png")
    assert await store.exists(key)
    assert await store.get(key) == b"hello"
    assert await store.url_for(key) == "http://localhost:8000/blobs/org1/creatives/abc/original.png"
    assert sha256_hex(b"hello").startswith("2cf24dba")


async def test_local_rejects_path_traversal(tmp_path):
    store = LocalBlobStore(tmp_path)
    import pytest

    with pytest.raises(ValueError):
        await store.put("../escape", b"x", "text/plain")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/core/test_blobs.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/cs/core/blobs.py`:

```python
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol

from cs.core.config import Settings


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def url_for(self, key: str, expires_seconds: int = 3600) -> str: ...


def _check_key(key: str) -> None:
    if key.startswith("/") or ".." in key.split("/") or key == "":
        raise ValueError(f"invalid blob key: {key!r}")


class LocalBlobStore:
    def __init__(self, root: Path | str, public_base_url: str = ""):
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        _check_key(key)
        return self.root / key

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    async def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        _check_key(key)
        return f"{self.public_base_url}/blobs/{key}"


class S3BlobStore:
    def __init__(self, bucket: str, endpoint_url: str | None = None):
        import boto3

        self.bucket = bucket
        self.client = boto3.client("s3", endpoint_url=endpoint_url)

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        _check_key(key)
        await asyncio.to_thread(
            self.client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
        )
        return key

    async def get(self, key: str) -> bytes:
        _check_key(key)
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)

    async def exists(self, key: str) -> bool:
        _check_key(key)
        try:
            await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False

    async def url_for(self, key: str, expires_seconds: int = 3600) -> str:
        _check_key(key)
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def blob_store_from_settings(settings: Settings) -> BlobStore:
    if settings.blob_backend == "s3":
        return S3BlobStore(settings.s3_bucket, settings.s3_endpoint_url)
    return LocalBlobStore(settings.blob_root, settings.public_base_url)
```

- [ ] **Step 4: Restore the `blobs` fixture in `tests/conftest.py`** (uncomment the import and fixture from Task 3).

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/core -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/cs/core/blobs.py tests/core/test_blobs.py tests/conftest.py
git commit -m "feat(core): blob store with local and S3 backends"
```

---

### Task 5: Job queue

**Files:**
- Create: `src/cs/core/queue.py`
- Test: `tests/core/test_queue.py`

**Interfaces:**
- Produces:
  - `@dataclass Job(id: UUID, org_id: UUID, job_type: str, payload: dict, attempts: int, max_attempts: int)`
  - `async enqueue(db, org_id: UUID, job_type: str, payload: dict | None = None, *, dedup_key: str | None = None, run_after: datetime | None = None, max_attempts: int = 5) -> UUID | None` (returns `None` when a pending/running job with the same `(job_type, dedup_key)` exists)
  - `async claim(db, worker_id: str, job_types: list[str] | None = None) -> Job | None`
  - `async complete(db, job_id: UUID) -> None`
  - `async fail(db, job_id: UUID, error: str) -> None` (re-queues with backoff `min(2**attempts, 300)` seconds until `attempts >= max_attempts`, then `dead`)
  - `async reclaim_stale(db, lease_seconds: int) -> int` (running jobs older than the lease go back to pending)

- [ ] **Step 1: Write the failing tests**

`tests/core/test_queue.py`:

```python
from datetime import UTC, datetime, timedelta

from cs.core import queue


async def test_enqueue_claim_complete(db, org_id):
    jid = await queue.enqueue(db, org_id, "echo", {"x": 1})
    job = await queue.claim(db, "w1")
    assert job is not None and job.id == jid and job.payload == {"x": 1}
    assert await queue.claim(db, "w2") is None  # locked
    await queue.complete(db, jid)
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "done"


async def test_dedup_key_blocks_duplicate_pending(db, org_id):
    a = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    b = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    assert a is not None and b is None
    await queue.complete(db, a)
    c = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    assert c is not None


async def test_fail_retries_then_dies(db, org_id):
    jid = await queue.enqueue(db, org_id, "flaky", max_attempts=2)
    await queue.claim(db, "w1")
    await queue.fail(db, jid, "boom")
    row = await db.fetchrow("SELECT status, attempts, run_after FROM job WHERE id=$1", jid)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["run_after"] > datetime.now(UTC)
    await db.execute("UPDATE job SET run_after = now() WHERE id=$1", jid)
    await queue.claim(db, "w1")
    await queue.fail(db, jid, "boom again")
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "dead"


async def test_run_after_and_job_type_filter(db, org_id):
    later = datetime.now(UTC) + timedelta(hours=1)
    await queue.enqueue(db, org_id, "later", run_after=later)
    await queue.enqueue(db, org_id, "now")
    assert await queue.claim(db, "w", job_types=["later"]) is None
    job = await queue.claim(db, "w", job_types=["now", "later"])
    assert job.job_type == "now"


async def test_reclaim_stale(db, org_id):
    jid = await queue.enqueue(db, org_id, "stuck")
    await queue.claim(db, "w1")
    await db.execute("UPDATE job SET locked_at = now() - interval '1 hour' WHERE id=$1", jid)
    assert await queue.reclaim_stale(db, lease_seconds=600) == 1
    assert (await queue.claim(db, "w2")).id == jid
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/core/test_queue.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`src/cs/core/queue.py`:

```python
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
        ON CONFLICT (job_type, dedup_key) WHERE status IN ('pending','running') AND dedup_key IS NOT NULL
        DO NOTHING
        RETURNING id
        """,
        jid, org_id, job_type, dedup_key, payload or {}, run_after, max_attempts,
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
        job_types, worker_id,
    )
    if not row:
        return None
    return Job(row["id"], row["org_id"], row["job_type"], row["payload"], row["attempts"], row["max_attempts"])


async def complete(db: Database, job_id: UUID) -> None:
    await db.execute(
        "UPDATE job SET status='done', finished_at=now(), locked_by=NULL WHERE id=$1 AND status='running'",
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
        job_id, error[:4000],
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/core/test_queue.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/cs/core/queue.py tests/core/test_queue.py
git commit -m "feat(core): postgres-backed job queue with dedup, backoff, and lease reclaim"
```

---

### Task 6: Job registry, worker loop, scheduler loop

**Files:**
- Create: `src/cs/jobs/__init__.py`, `src/cs/jobs/registry.py`, `src/cs/jobs/worker.py`, `src/cs/jobs/scheduler.py`, `src/cs/worker.py`, `src/cs/scheduler.py`
- Test: `tests/jobs/test_worker.py`, `tests/jobs/test_scheduler.py`

**Interfaces:**
- Consumes: `cs.core.queue` (Task 5), `cs.core.blobs.BlobStore` (Task 4), `Settings` (Task 1).
- Produces:
  - `@dataclass JobContext(db: Database, blobs: BlobStore, settings: Settings, job: Job)`
  - `handler(job_type: str)` decorator registering `async def fn(ctx: JobContext) -> None` in `HANDLERS: dict[str, Handler]`
  - `async run_once(db, blobs, settings, worker_id) -> bool` (claims and runs one job; returns whether a job ran)
  - `async run_worker(settings)` (infinite loop; `python -m cs.worker`)
  - `SCHEDULES: list[Schedule]` where `Schedule(name, every_seconds, enqueue_fn)`; `enqueue_fn(db, settings) -> None`
  - `async run_scheduler_tick(db, settings, state: dict[str, float], now: float) -> list[str]` (names of schedules fired)
  - `register_schedule(name, every_seconds)` decorator

- [ ] **Step 1: Write the failing worker test**

`tests/jobs/test_worker.py`:

```python
from cs.core import queue
from cs.jobs import registry
from cs.jobs.worker import run_once


async def test_run_once_executes_handler_and_completes(db, blobs, settings, org_id):
    seen = []

    @registry.handler("test.echo")
    async def echo(ctx: registry.JobContext) -> None:
        seen.append(ctx.job.payload["msg"])

    jid = await queue.enqueue(db, org_id, "test.echo", {"msg": "hi"})
    assert await run_once(db, blobs, settings, "w1") is True
    assert seen == ["hi"]
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "done"
    assert await run_once(db, blobs, settings, "w1") is False


async def test_run_once_marks_failure(db, blobs, settings, org_id):
    @registry.handler("test.boom")
    async def boom(ctx: registry.JobContext) -> None:
        raise ValueError("nope")

    jid = await queue.enqueue(db, org_id, "test.boom", max_attempts=1)
    await run_once(db, blobs, settings, "w1")
    row = await db.fetchrow("SELECT status, error FROM job WHERE id=$1", jid)
    assert row["status"] == "dead" and "nope" in row["error"]


async def test_unknown_job_type_is_failed_not_crashed(db, blobs, settings, org_id):
    jid = await queue.enqueue(db, org_id, "test.unknown", max_attempts=1)
    await run_once(db, blobs, settings, "w1")
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "dead"
```

- [ ] **Step 2: Write the failing scheduler test**

`tests/jobs/test_scheduler.py`:

```python
from cs.jobs import scheduler


async def test_tick_fires_due_schedules_only(db, settings):
    fired = []

    @scheduler.register_schedule("test.every10", every_seconds=10)
    async def every10(db_, settings_):
        fired.append("every10")

    state: dict[str, float] = {}
    assert scheduler_names(await scheduler.run_scheduler_tick(db, settings, state, now=1000.0)) == ["test.every10"]
    assert await scheduler.run_scheduler_tick(db, settings, state, now=1005.0) == []
    assert scheduler_names(await scheduler.run_scheduler_tick(db, settings, state, now=1011.0)) == ["test.every10"]
    assert fired == ["every10", "every10"]


def scheduler_names(names):
    return [n for n in names if n.startswith("test.")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/jobs -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Implement registry**

`src/cs/jobs/__init__.py`: empty.

`src/cs/jobs/registry.py`:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.queue import Job


@dataclass
class JobContext:
    db: Database
    blobs: BlobStore
    settings: Settings
    job: Job


Handler = Callable[[JobContext], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(job_type: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn

    return deco
```

- [ ] **Step 5: Implement worker**

`src/cs/jobs/worker.py`:

```python
from __future__ import annotations

import asyncio
import logging
import socket
import traceback

from cs.core import queue
from cs.core.blobs import BlobStore, blob_store_from_settings
from cs.core.config import Settings, get_settings
from cs.core.db import Database
from cs.jobs.registry import HANDLERS, JobContext

log = logging.getLogger("cs.worker")


async def run_once(db: Database, blobs: BlobStore, settings: Settings, worker_id: str) -> bool:
    job = await queue.claim(db, worker_id)
    if job is None:
        return False
    fn = HANDLERS.get(job.job_type)
    if fn is None:
        await queue.fail(db, job.id, f"no handler registered for {job.job_type}")
        return True
    try:
        await fn(JobContext(db=db, blobs=blobs, settings=settings, job=job))
        await queue.complete(db, job.id)
        log.info("job done", extra={"job_id": str(job.id), "job_type": job.job_type, "org_id": str(job.org_id)})
    except Exception as exc:  # noqa: BLE001 - worker must survive any handler failure
        await queue.fail(db, job.id, f"{exc}\n{traceback.format_exc()}")
        log.exception("job failed", extra={"job_id": str(job.id), "job_type": job.job_type})
    return True


async def run_worker(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    import cs.jobs.all_handlers  # noqa: F401 - registers handlers from every module

    db = await Database.connect(settings.database_url)
    blobs = blob_store_from_settings(settings)
    worker_id = f"{socket.gethostname()}:{id(db)}"
    log.info("worker started", extra={"worker_id": worker_id})
    try:
        while True:
            ran = await run_once(db, blobs, settings, worker_id)
            if not ran:
                await asyncio.sleep(settings.worker_poll_seconds)
    finally:
        await db.close()
```

`src/cs/jobs/all_handlers.py` (grows in later phases):

```python
"""Import every module that registers job handlers so the worker sees them."""
```

`src/cs/worker.py`:

```python
import asyncio
import logging

from cs.jobs.worker import run_worker

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_worker())
```

- [ ] **Step 6: Implement scheduler**

`src/cs/jobs/scheduler.py`:

```python
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cs.core import queue
from cs.core.config import Settings, get_settings
from cs.core.db import Database

log = logging.getLogger("cs.scheduler")

EnqueueFn = Callable[[Database, Settings], Awaitable[None]]


@dataclass
class Schedule:
    name: str
    every_seconds: int
    fn: EnqueueFn


SCHEDULES: list[Schedule] = []


def register_schedule(name: str, every_seconds: int) -> Callable[[EnqueueFn], EnqueueFn]:
    def deco(fn: EnqueueFn) -> EnqueueFn:
        SCHEDULES.append(Schedule(name, every_seconds, fn))
        return fn

    return deco


async def run_scheduler_tick(
    db: Database, settings: Settings, state: dict[str, float], now: float
) -> list[str]:
    fired: list[str] = []
    for s in SCHEDULES:
        last = state.get(s.name, float("-inf"))
        if now - last >= s.every_seconds:
            try:
                await s.fn(db, settings)
                fired.append(s.name)
            except Exception:  # noqa: BLE001
                log.exception("schedule failed", extra={"schedule": s.name})
            state[s.name] = now
    return fired


@register_schedule("core.reclaim_stale_jobs", every_seconds=60)
async def reclaim_stale_jobs(db: Database, settings: Settings) -> None:
    n = await queue.reclaim_stale(db, settings.job_lease_seconds)
    if n:
        log.warning("reclaimed stale jobs", extra={"count": n})


async def run_scheduler(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    import cs.jobs.all_handlers  # noqa: F401 - schedules live beside handlers

    db = await Database.connect(settings.database_url)
    state: dict[str, float] = {}
    try:
        while True:
            await run_scheduler_tick(db, settings, state, time.time())
            await asyncio.sleep(5)
    finally:
        await db.close()
```

`src/cs/scheduler.py`:

```python
import asyncio
import logging

from cs.jobs.scheduler import run_scheduler

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asyncio.run(run_scheduler())
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/jobs -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add src/cs/jobs src/cs/worker.py src/cs/scheduler.py tests/jobs
git commit -m "feat(jobs): handler registry, worker loop, scheduler loop"
```

---

### Task 7: Auth and API skeleton

**Files:**
- Create: `src/cs/orgs/__init__.py`, `src/cs/orgs/repo.py`, `src/cs/api/__init__.py`, `src/cs/api/app.py`, `src/cs/api/auth.py`, `src/cs/api/deps.py`, `src/cs/api/routers/__init__.py`, `src/cs/api/routers/health.py`, `src/cs/api/routers/blobs.py`, `src/cs/api/__main__.py`
- Test: `tests/api/test_health_and_auth.py`
- Modify: `tests/conftest.py` (add `client` fixture)

**Interfaces:**
- Produces:
  - `AuthContext(user_id: str, org_id: UUID, role: str)`
  - `current_auth` FastAPI dependency. In `auth_mode="dev"` it reads headers `X-Dev-User`, `X-Dev-Org` (a Clerk-style org id string), `X-Dev-Role` (default `admin`). In `auth_mode="clerk"` it verifies `Authorization: Bearer <jwt>` against `clerk_jwks_url` and reads claims `sub`, `org_id`, `org_role`. Either way it resolves or creates the `org` row and membership.
  - `require_admin` dependency.
  - `create_app(settings, db, blobs) -> FastAPI`; `app.state.db`, `app.state.blobs`, `app.state.settings`.
  - `deps.get_db`, `deps.get_blobs`, `deps.get_settings_dep` for routers.
  - Routes: `GET /health`, `GET /me`, `GET /blobs/{key:path}` (serves local blobs; 404 in s3 mode).
  - `cs.orgs.repo.get_or_create_org(db, clerk_org_id: str, name: str) -> UUID`, `ensure_membership(db, org_id, user_id, role) -> None`.
  - Test fixture `client` (httpx.AsyncClient against the app) and `dev_headers(org="org_test", user="user_1", role="admin") -> dict`.

- [ ] **Step 1: Write the failing test**

`tests/api/test_health_and_auth.py`:

```python
from tests.conftest import dev_headers


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_me_requires_auth(client):
    assert (await client.get("/me")).status_code == 401


async def test_me_provisions_org_and_membership(client, db):
    r = await client.get("/me", headers=dev_headers(org="org_abc", user="user_9"))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user_9" and body["role"] == "admin"
    org = await db.fetchrow("SELECT id FROM org WHERE clerk_org_id='org_abc'")
    assert str(org["id"]) == body["org_id"]
    assert await db.fetchval("SELECT count(*) FROM user_org WHERE user_id='user_9'") == 1


async def test_blob_route_serves_local_file(client, blobs):
    await blobs.put("o/x.txt", b"data", "text/plain")
    r = await client.get("/blobs/o/x.txt")
    assert r.status_code == 200 and r.content == b"data"
```

- [ ] **Step 2: Add client fixture to conftest**

Append to `tests/conftest.py`:

```python
import httpx

from cs.api.app import create_app


def dev_headers(org: str = "org_test", user: str = "user_1", role: str = "admin") -> dict:
    return {"X-Dev-User": user, "X-Dev-Org": org, "X-Dev-Role": role}


@pytest.fixture
async def client(settings, db, blobs):
    app = create_app(settings, db, blobs)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/api -v`
Expected: FAIL with `ModuleNotFoundError: cs.api`

- [ ] **Step 4: Implement orgs repo**

`src/cs/orgs/__init__.py`: empty.

`src/cs/orgs/repo.py`:

```python
from uuid import UUID

from cs.core.db import Database
from cs.core.ids import new_id


async def get_or_create_org(db: Database, clerk_org_id: str, name: str) -> UUID:
    row = await db.fetchrow("SELECT id FROM org WHERE clerk_org_id = $1", clerk_org_id)
    if row:
        return row["id"]
    oid = new_id()
    row = await db.fetchrow(
        """
        INSERT INTO org (id, name, clerk_org_id) VALUES ($1, $2, $3)
        ON CONFLICT (clerk_org_id) DO UPDATE SET name = org.name
        RETURNING id
        """,
        oid, name, clerk_org_id,
    )
    return row["id"]


async def ensure_membership(db: Database, org_id: UUID, user_id: str, role: str) -> None:
    await db.execute(
        """
        INSERT INTO user_org (user_id, org_id, role) VALUES ($1, $2, $3)
        ON CONFLICT (user_id, org_id) DO UPDATE SET role = EXCLUDED.role
        """,
        user_id, org_id, role,
    )
```

- [ ] **Step 5: Implement auth**

`src/cs/api/__init__.py`, `src/cs/api/routers/__init__.py`: empty.

`src/cs/api/deps.py`:

```python
from fastapi import Request

from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings
```

`src/cs/api/auth.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request

from cs.api.deps import get_db, get_settings_dep
from cs.core.config import Settings
from cs.core.db import Database
from cs.orgs.repo import ensure_membership, get_or_create_org


@dataclass
class AuthContext:
    user_id: str
    org_id: UUID
    role: str


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url)


def _clerk_claims(request: Request, settings: Settings) -> tuple[str, str, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = header.split(" ", 1)[1]
    try:
        key = _jwks_client(settings.clerk_jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.clerk_issuer or None)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    org = claims.get("org_id")
    if not org:
        raise HTTPException(403, "no active organization")
    role = "admin" if str(claims.get("org_role", "")).endswith("admin") else "member"
    return claims["sub"], org, role


def _dev_claims(request: Request) -> tuple[str, str, str]:
    user = request.headers.get("x-dev-user")
    org = request.headers.get("x-dev-org")
    if not user or not org:
        raise HTTPException(401, "dev auth requires X-Dev-User and X-Dev-Org")
    return user, org, request.headers.get("x-dev-role", "admin")


async def current_auth(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    if settings.auth_mode == "clerk":
        user_id, clerk_org, role = _clerk_claims(request, settings)
    else:
        user_id, clerk_org, role = _dev_claims(request)
    org_id = await get_or_create_org(db, clerk_org, name=clerk_org)
    await ensure_membership(db, org_id, user_id, role)
    return AuthContext(user_id=user_id, org_id=org_id, role=role)


async def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth
```

- [ ] **Step 6: Implement routers and app**

`src/cs/api/routers/health.py`:

```python
from fastapi import APIRouter, Depends

from cs.api.auth import AuthContext, current_auth

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"ok": True}


@router.get("/me")
async def me(auth: AuthContext = Depends(current_auth)) -> dict:
    return {"user_id": auth.user_id, "org_id": str(auth.org_id), "role": auth.role}
```

`src/cs/api/routers/blobs.py`:

```python
import mimetypes

from fastapi import APIRouter, Depends, HTTPException, Response

from cs.api.deps import get_blobs
from cs.core.blobs import BlobStore, LocalBlobStore

router = APIRouter()


@router.get("/blobs/{key:path}")
async def get_blob(key: str, blobs: BlobStore = Depends(get_blobs)) -> Response:
    if not isinstance(blobs, LocalBlobStore):
        raise HTTPException(404, "blobs are served from object storage")
    try:
        if not await blobs.exists(key):
            raise HTTPException(404)
        data = await blobs.get(key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    media, _ = mimetypes.guess_type(key)
    return Response(content=data, media_type=media or "application/octet-stream")
```

`src/cs/api/app.py`:

```python
from fastapi import FastAPI

from cs.api.routers import blobs, health
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database


def create_app(settings: Settings, db: Database, blobs_store: BlobStore) -> FastAPI:
    app = FastAPI(title="creative-strategist")
    app.state.settings = settings
    app.state.db = db
    app.state.blobs = blobs_store
    app.include_router(health.router)
    app.include_router(blobs.router)
    return app
```

`src/cs/api/__main__.py`:

```python
import asyncio
import logging

import uvicorn

from cs.api.app import create_app
from cs.core.blobs import blob_store_from_settings
from cs.core.config import get_settings
from cs.core.db import Database


async def _serve() -> None:
    settings = get_settings()
    db = await Database.connect(settings.database_url)
    app = create_app(settings, db, blob_store_from_settings(settings))
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="info")
    await uvicorn.Server(config).serve()
    await db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_serve())
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/api -v`
Expected: PASS (4 tests)

- [ ] **Step 8: Commit**

```bash
git add src/cs/api src/cs/orgs tests/api tests/conftest.py
git commit -m "feat(api): app skeleton with dev/clerk auth and org provisioning"
```

---

### Task 8: CI and README

**Files:**
- Create: `.github/workflows/ci.yml`, `README.md`

- [ ] **Step 1: Write CI workflow**

`.github/workflows/ci.yml`:

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_USER: cs, POSTGRES_PASSWORD: cs, POSTGRES_DB: cs_test }
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U cs" --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      DATABASE_URL: postgres://cs:cs@localhost:5432/cs_test?sslmode=disable
      DATABASE_URL_TEST: postgres://cs:cs@localhost:5432/cs_test?sslmode=disable
      TOKEN_ENCRYPTION_KEY: dGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXktdGVzdC1rZXk=
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: sudo apt-get update && sudo apt-get install -y ffmpeg
      - run: curl -fsSL -o /usr/local/bin/dbmate https://github.com/amacneil/dbmate/releases/latest/download/dbmate-linux-amd64 && sudo chmod +x /usr/local/bin/dbmate
      - run: uv sync --extra dev
      - run: make lint
      - run: make test
```

- [ ] **Step 2: Write README**

`README.md`:

```markdown
# creative-strategist

AI creative strategist for paid social. See `docs/superpowers/specs/` for the design and `docs/superpowers/plans/` for the build plans.

## Local setup

    brew install uv dbmate ffmpeg          # macOS
    cp .env.example .env                    # fill TOKEN_ENCRYPTION_KEY at minimum
    make up                                 # postgres + minio
    make install migrate
    make test

## Processes

    make api        # http://localhost:8000
    make worker     # runs jobs from the `job` table
    make scheduler  # enqueues recurring jobs
```

- [ ] **Step 3: Run lint and full tests**

Run: `make lint && make test`
Expected: lint clean; all Phase 0 tests PASS

- [ ] **Step 4: Commit**

```bash
git add .github README.md
git commit -m "chore: CI workflow and README"
```

---

## Self-review

- **Spec coverage:** Section 3 processes (api, worker, scheduler) → Tasks 6, 7. Section 4 conventions (uuid7, jsonb, raw SQL, one-way status) → Tasks 2, 3, 5. Section 11 job lifecycle, lease reclaim, dead-letter, structured logging fields → Tasks 5, 6. Section 13 local dev → Tasks 1, 8. Auth via Clerk with org scoping → Task 7. Blob rule → Task 4.
- **Type consistency:** `Job` fields used in `worker.run_once` match `queue.Job`. `JobContext` fields (`db`, `blobs`, `settings`, `job`) are the names every later handler uses. `dev_headers` lives in `tests/conftest.py` and is imported by API tests in later phases.
- **Deferred:** `cs.jobs.all_handlers` is an empty module here; each later phase appends an import line to it.
