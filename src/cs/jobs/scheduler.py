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
