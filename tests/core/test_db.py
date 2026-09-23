from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from cs.core.db import _json_default
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


async def test_job_dedup_index_scoped_by_org(db):
    org_a, org_b = new_id(), new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'org a')", org_a)
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'org b')", org_b)

    for org_id in (org_a, org_b):
        await db.execute(
            "INSERT INTO job (id, org_id, job_type, dedup_key, status) "
            "VALUES ($1, $2, 'render', 'shared-key', 'pending')",
            new_id(),
            org_id,
        )


async def test_jsonb_codec_encodes_uuid_datetime_and_decimal(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'codec')", oid)
    payload = {
        "ad_account_id": new_id(),
        "since": datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        "on": date(2026, 9, 4),
        "spend": Decimal("12.34"),
    }
    jid = new_id()
    await db.execute(
        "INSERT INTO job (id, org_id, job_type, payload, status)"
        " VALUES ($1, $2, 'codec', $3, 'pending')",
        jid,
        oid,
        payload,
    )
    stored = await db.fetchval("SELECT payload FROM job WHERE id=$1", jid)
    assert stored["ad_account_id"] == str(payload["ad_account_id"])
    assert stored["since"] == "2026-09-04T12:30:00+00:00"
    assert stored["on"] == "2026-09-04"
    assert stored["spend"] == "12.34"


def test_json_default_rejects_unknown_types():
    with pytest.raises(TypeError):
        _json_default(object())


def test_pool_kwargs_disable_statement_cache_for_transaction_pooler():
    from cs.core.db import pool_kwargs

    session = pool_kwargs("postgres://u:p@aws-0.pooler.supabase.com:5432/postgres")
    tx = pool_kwargs("postgres://u:p@aws-0.pooler.supabase.com:6543/postgres")
    assert "statement_cache_size" not in session
    assert tx["statement_cache_size"] == 0


def test_every_pool_bounds_its_commands():
    """A pooler that stops answering must surface as an error, not as a hang. On 2026-09-22
    the worker waited four and a half hours on a write it could never finish, holding a lease
    it could not renew, and lost a finished account read (two paid model calls) with it."""
    from cs.core.db import DEFAULT_COMMAND_TIMEOUT, pool_kwargs

    worker = pool_kwargs("postgres://u:p@aws-0.pooler.supabase.com:6543/postgres")
    assert worker["command_timeout"] == DEFAULT_COMMAND_TIMEOUT

    # The API's own bound is tighter, and replaces it rather than adding to it.
    api = pool_kwargs("postgres://u:p@aws-0.pooler.supabase.com:6543/postgres", 15_000)
    assert api["command_timeout"] == 20.0
    assert api["command_timeout"] < DEFAULT_COMMAND_TIMEOUT


def test_settings_pool_url_falls_back_to_database_url(monkeypatch):
    from cs.core.config import Settings

    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@h:5432/d")
    monkeypatch.setenv("CLERK_JWKS_URL", "https://example.test/jwks")
    monkeypatch.delenv("DATABASE_POOL_URL", raising=False)
    assert Settings().pool_url == "postgres://u:p@h:5432/d"
    monkeypatch.setenv("DATABASE_POOL_URL", "postgres://u:p@h:6543/d")
    assert Settings().pool_url == "postgres://u:p@h:6543/d"
