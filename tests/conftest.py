import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

import cs.jobs.all_handlers  # noqa: F401
from cs.api.app import create_app
from cs.core.blobs import LocalBlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.ids import new_id

ROOT = Path(__file__).resolve().parents[1]
DBMATE = shutil.which("dbmate") or str(Path.home() / ".local/bin/dbmate")


@pytest.fixture(scope="session")
def settings() -> Settings:
    os.environ.setdefault("TOKEN_ENCRYPTION_KEY", "piCYmkw56Goak3cRFwGNnH1Rze5W0zL1jcO2MXu27Ww=")
    os.environ.setdefault("DATABASE_URL", "postgres://cs:cs@127.0.0.1:5432/cs?sslmode=disable")
    os.environ.setdefault(
        "DATABASE_URL_TEST", "postgres://cs:cs@127.0.0.1:5432/cs_test?sslmode=disable"
    )
    os.environ.setdefault("AUTH_MODE", "dev")
    os.environ.setdefault("BLOB_BACKEND", "local")
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


def dev_headers(org: str = "org_test", user: str = "user_1", role: str = "admin") -> dict:
    return {"X-Dev-User": user, "X-Dev-Org": org, "X-Dev-Role": role}


@pytest.fixture
async def client(settings, db, blobs):
    app = create_app(settings, db, blobs)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
