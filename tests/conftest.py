import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi import Depends, HTTPException, Request

import cs.jobs.all_handlers  # noqa: F401
from cs.api.app import create_app
from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db
from cs.core import repo
from cs.core.blobs import LocalBlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id
from cs.core.repo import metrics as metrics_repo
from cs.core.repo import rollups
from cs.orgs.repo import ensure_membership, get_or_create_org

ROOT = Path(__file__).resolve().parents[1]
DBMATE = shutil.which("dbmate") or str(Path.home() / ".local/bin/dbmate")


@pytest.fixture(scope="session")
def settings() -> Settings:
    os.environ.setdefault("DATABASE_URL", "postgres://cs:cs@127.0.0.1:5432/cs?sslmode=disable")
    os.environ.setdefault(
        "DATABASE_URL_TEST", "postgres://cs:cs@127.0.0.1:5432/cs_test?sslmode=disable"
    )
    os.environ.setdefault("BLOB_BACKEND", "local")
    # Never a real JWKS endpoint: the test client never exercises _clerk_claims at
    # all (see `client` fixture below), so this only needs to satisfy Settings'
    # required-field check.
    os.environ.setdefault("CLERK_JWKS_URL", "https://example.test/.well-known/jwks.json")
    s = Settings()
    subprocess.run(
        [
            DBMATE,
            "--url",
            s.database_url_test,
            "--no-dump-schema",
            "--migrations-dir",
            str(ROOT / "db/migrations"),
            "up",
        ],
        check=True,
        capture_output=True,
    )
    return s


@pytest.fixture
async def db(settings):
    database = await Database.connect(settings.database_url_test)
    tables = await database.fetch(
        "SELECT tablename FROM pg_tables"
        " WHERE schemaname='public' AND tablename <> 'schema_migrations'"
    )
    names = ", ".join(f'"{t["tablename"]}"' for t in tables)
    if names:
        await database.execute(f"TRUNCATE {names} CASCADE")
    await rollups.refresh(database)  # TRUNCATE does not empty a materialized view
    yield database
    await database.close()


@pytest.fixture(autouse=True)
def _rollups_follow_metrics(monkeypatch):
    """In production the thirty-day rollups refresh when a sync ends and hourly. A test that
    writes metrics expects to read them back at once, so here every write refreshes them."""
    real = metrics_repo.upsert_metrics

    async def upsert_then_refresh(db, *args, **kwargs):
        out = await real(db, *args, **kwargs)
        await rollups.refresh(db)
        return out

    for mod in (repo, metrics_repo):
        monkeypatch.setattr(mod, "upsert_metrics", upsert_then_refresh)


@pytest.fixture
async def org_id(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'test org')", oid)
    return oid


@pytest.fixture
async def ad_account_id(db, org_id):
    aid = new_id()
    await db.execute(
        "INSERT INTO ad_account (id, org_id, platform, platform_account_id, name)"
        " VALUES ($1, $2, 'meta', 'act_test', 'test account')",
        aid,
        org_id,
    )
    return aid


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


def dev_headers(org: str = "org_test", user: str = "user_1", role: str = "admin") -> dict:
    return {"X-Dev-User": user, "X-Dev-Org": org, "X-Dev-Role": role}


async def _test_auth(request: Request, db: Database = Depends(get_db)) -> AuthContext:
    """Test-only stand-in for `current_auth`, wired in below via FastAPI's
    dependency_overrides -- never reachable through the real app or a Settings
    flag. Trusts the same X-Dev-* headers `dev_headers()` builds, so none of the
    tests using it need to change; only where the trust decision is made moves,
    from a runtime AUTH_MODE flag to a mechanism that exists only inside pytest."""
    user = request.headers.get("x-dev-user")
    org = request.headers.get("x-dev-org")
    if not user or not org:
        raise HTTPException(401, "test auth requires X-Dev-User and X-Dev-Org")
    role = request.headers.get("x-dev-role", "admin")
    org_id = await get_or_create_org(db, org, name=org)
    await ensure_membership(db, org_id, user, role)
    return AuthContext(user_id=user, org_id=org_id, role=role)


@pytest.fixture
async def client(settings, db, blobs):
    app = create_app(settings, db, blobs)
    app.dependency_overrides[current_auth] = _test_auth
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
