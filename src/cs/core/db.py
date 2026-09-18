from __future__ import annotations

import datetime
import decimal
import functools
import json
import uuid
from collections.abc import Iterable, Sequence
from contextlib import asynccontextmanager
from typing import Any

import asyncpg


def _json_default(o: Any) -> str:
    """Encode the few non-JSON types that reach jsonb payloads."""
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, datetime.datetime | datetime.date):
        return o.isoformat()
    if isinstance(o, decimal.Decimal):
        return str(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


_json_dumps = functools.partial(json.dumps, default=_json_default)


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb", encoder=_json_dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )
    await conn.set_type_codec(
        "json", encoder=_json_dumps, decoder=json.loads, schema="pg_catalog", format="text"
    )


# Supabase's transaction-mode pooler (port 6543) silently drops a connection idle for
# somewhere between 90 s and 3 min, and the session-mode port between 3 and 5 min (measured
# on the EC2 box, 2026-09-18). asyncpg's default keeps idle connections for 5 min and checks
# nothing on checkout, so the first query after a lull landed on a dead socket and hung or
# raised ConnectionDoesNotExistError. Recycle well inside the pooler's window.
POOL_IDLE_LIFETIME = 60.0


def pool_kwargs(url: str) -> dict:
    """Pool settings for `url`. Behind a transaction-mode pooler (PgBouncer, Supabase port
    6543) server connections are shared per transaction, so asyncpg's prepared-statement
    cache must be off: a statement prepared on one server connection does not exist on
    the next."""
    kwargs: dict = {
        "min_size": 1,
        "max_size": 10,
        "init": _init_conn,
        "max_inactive_connection_lifetime": POOL_IDLE_LIFETIME,
    }
    if ":6543/" in url or url.rstrip("/").endswith(":6543"):
        kwargs["statement_cache_size"] = 0
    return kwargs


def _dead_connection(exc: Exception) -> bool:
    """True for the errors a silently-closed pooled connection raises on its next use:
    asyncpg's own ConnectionDoesNotExistError / ConnectionFailureError, or its
    InterfaceError once it has noticed the socket is gone. Other InterfaceErrors are
    caller mistakes and are not retried."""
    if isinstance(exc, asyncpg.exceptions.PostgresConnectionError):
        return True
    return isinstance(exc, asyncpg.InterfaceError) and "closed" in str(exc)


class Database:
    def __init__(self, pool: asyncpg.Pool | None, conn: asyncpg.Connection | None = None):
        self._pool = pool
        self._conn = conn

    @classmethod
    async def connect(cls, url: str) -> Database:
        pool = await asyncpg.create_pool(url, **pool_kwargs(url))
        return cls(pool)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()

    def _target(self):
        return self._conn if self._conn is not None else self._pool

    async def _run(self, method: str, *args: Any) -> Any:
        """Run one pool method, retrying once when the connection it drew turns out to be
        dead (the pooler closed it while idle; the pool discards it on the way back). A held
        connection inside a transaction is never retried: its earlier statements died with
        it, and replaying one of them alone would silently lose the rest."""
        target = self._target()
        try:
            return await getattr(target, method)(*args)
        except Exception as exc:
            if self._conn is not None or not _dead_connection(exc):
                raise
        return await getattr(target, method)(*args)

    async def fetch(self, sql: str, *args: Any) -> list[asyncpg.Record]:
        return await self._run("fetch", sql, *args)

    async def fetchrow(self, sql: str, *args: Any) -> asyncpg.Record | None:
        return await self._run("fetchrow", sql, *args)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        return await self._run("fetchval", sql, *args)

    async def execute(self, sql: str, *args: Any) -> str:
        return await self._run("execute", sql, *args)

    async def executemany(self, sql: str, args: Iterable[Sequence[Any]]) -> None:
        await self._run("executemany", sql, args)

    @asynccontextmanager
    async def transaction(self):
        if self._conn is not None:
            async with self._conn.transaction():
                yield self
            return
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                yield Database(None, conn)
