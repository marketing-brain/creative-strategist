from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from cs.core import queue
from cs.core.config import Settings, get_settings
from cs.core.db import Database
from cs.core.repo import rollups

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


async def load_schedule_state(db: Database) -> dict[str, float]:
    """Last run of every schedule, as epoch seconds, so restarts do not re-fire."""
    rows = await db.fetch("SELECT name, last_run_at FROM schedule_state")
    return {r["name"]: r["last_run_at"].timestamp() for r in rows}


async def schedule_paused(db: Database, name: str) -> bool:
    """A schedule is paused by setting its last run far in the future, so it never comes due."""
    return bool(
        await db.fetchval("SELECT last_run_at > now() FROM schedule_state WHERE name = $1", name)
    )


async def _record_run(db: Database, name: str, now: float) -> None:
    await db.execute(
        """
        INSERT INTO schedule_state (name, last_run_at) VALUES ($1, to_timestamp($2))
        ON CONFLICT (name) DO UPDATE SET last_run_at = EXCLUDED.last_run_at
        """,
        name,
        now,
    )


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
            await _record_run(db, s.name, now)
    return fired


@register_schedule("core.reclaim_stale_jobs", every_seconds=60)
async def reclaim_stale_jobs(db: Database, settings: Settings) -> None:
    n = await queue.reclaim_stale(db, settings.job_lease_seconds)
    if n:
        log.warning("reclaimed stale jobs", extra={"count": n})


@register_schedule("core.purge_done_jobs", every_seconds=86400)
async def purge_done_jobs(db: Database, settings: Settings) -> None:
    n = await queue.purge_done(db, settings.job_retention_days)
    if n:
        log.info("purged finished jobs", extra={"count": n})


@register_schedule("core.refresh_metric_rollups", every_seconds=3600)
async def refresh_metric_rollups(db: Database, settings: Settings) -> None:
    # The rollups cover "the last thirty days" as of their last refresh, so the window has
    # to keep moving on days when no sync lands. Costs no paid call, so it is never paused.
    await rollups.refresh(db)


async def run_scheduler(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    import cs.jobs.all_handlers  # noqa: F401 - schedules live beside handlers

    db = await Database.connect(settings.pool_url)
    state = await load_schedule_state(db)
    try:
        while True:
            await run_scheduler_tick(db, settings, state, time.time())
            await asyncio.sleep(5)
    finally:
        await db.close()
