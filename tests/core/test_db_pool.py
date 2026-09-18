"""The app pools sit behind Supabase's transaction-mode pooler, which silently drops a
connection idle for a couple of minutes. asyncpg validates nothing on checkout, so the first
query after a lull used to hang or fail with ConnectionDoesNotExistError. These tests pin the
two defences: recycle idle connections before the pooler does, and retry a query once when
its connection turns out to be dead."""

import asyncpg
import pytest

from cs.core.db import POOL_IDLE_LIFETIME, Database, pool_kwargs

POOLER = "postgres://u:p@aws-0-us-west-2.pooler.supabase.com:6543/postgres?sslmode=require"
DIRECT = "postgres://u:p@db.example.supabase.co:5432/postgres?sslmode=require"


def test_pool_recycles_idle_connections_before_the_pooler_drops_them():
    assert POOL_IDLE_LIFETIME == 60.0
    assert pool_kwargs(POOLER)["max_inactive_connection_lifetime"] == 60.0
    assert pool_kwargs(DIRECT)["max_inactive_connection_lifetime"] == 60.0


class FakePool:
    """Raises the given exceptions in order, one per call, then returns `value`."""

    def __init__(self, failures, value="ok"):
        self.failures = list(failures)
        self.value = value
        self.calls = []

    async def _call(self, sql, *args):
        self.calls.append((sql, args))
        if self.failures:
            raise self.failures.pop(0)
        return self.value

    fetch = fetchrow = fetchval = execute = _call

    async def executemany(self, sql, args):
        return await self._call(sql, tuple(args))


async def test_retries_once_when_the_pooled_connection_is_dead():
    pool = FakePool([asyncpg.exceptions.ConnectionDoesNotExistError("closed mid-operation")])
    db = Database(pool)
    assert await db.fetchrow("SELECT 1") == "ok"
    assert len(pool.calls) == 2


async def test_retries_on_asyncpg_closed_connection_interface_error():
    pool = FakePool([asyncpg.InterfaceError("connection is closed")])
    assert await Database(pool).fetchval("SELECT 1") == "ok"


async def test_does_not_retry_a_second_dead_connection():
    pool = FakePool(
        [
            asyncpg.exceptions.ConnectionDoesNotExistError("first"),
            asyncpg.exceptions.ConnectionDoesNotExistError("second"),
        ]
    )
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError, match="second"):
        await Database(pool).execute("UPDATE x SET y = 1")
    assert len(pool.calls) == 2


async def test_does_not_retry_other_interface_errors():
    pool = FakePool([asyncpg.InterfaceError("the server expects 1 argument for this query")])
    with pytest.raises(asyncpg.InterfaceError, match="expects 1 argument"):
        await Database(pool).fetch("SELECT $1")
    assert len(pool.calls) == 1


async def test_does_not_retry_inside_a_transaction():
    # A held connection carries the transaction's earlier statements; retrying one statement
    # on it after the connection died would silently drop the rest.
    conn = FakePool([asyncpg.exceptions.ConnectionDoesNotExistError("gone")])
    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await Database(None, conn).execute("INSERT INTO t VALUES (1)")
    assert len(conn.calls) == 1
