import time

import pytest

from cs.core.ids import new_id
from cs.jobs import scheduler


async def test_tick_fires_due_schedules_only(db, settings):
    fired = []

    @scheduler.register_schedule("test.every10", every_seconds=10)
    async def every10(db_, settings_):
        fired.append("every10")

    state: dict[str, float] = {}
    assert scheduler_names(await scheduler.run_scheduler_tick(db, settings, state, now=1000.0)) == [
        "test.every10"
    ]
    assert await scheduler.run_scheduler_tick(db, settings, state, now=1005.0) == []
    assert scheduler_names(await scheduler.run_scheduler_tick(db, settings, state, now=1011.0)) == [
        "test.every10"
    ]
    assert fired == ["every10", "every10"]


def scheduler_names(names):
    return [n for n in names if n.startswith("test.")]


async def test_schedule_state_is_persisted_and_reloadable(db, settings):
    runs = []

    @scheduler.register_schedule("test.persisted", every_seconds=600)
    async def persisted(db_, settings_):
        runs.append("persisted")

    now = time.time()
    assert "test.persisted" in await scheduler.run_scheduler_tick(db, settings, {}, now)

    # A fresh process starts from what the table remembers, not from an empty dict.
    loaded = await scheduler.load_schedule_state(db)
    assert loaded["test.persisted"] == pytest.approx(now, abs=1e-3)
    assert "test.persisted" not in await scheduler.run_scheduler_tick(db, settings, loaded, now + 5)
    assert runs == ["persisted"]

    assert "test.persisted" in await scheduler.run_scheduler_tick(db, settings, loaded, now + 601)
    assert (await scheduler.load_schedule_state(db))["test.persisted"] == pytest.approx(
        now + 601, abs=1e-3
    )


async def test_purge_done_jobs_keeps_dead_and_live_rows(db, org_id):
    from datetime import UTC, datetime, timedelta

    from cs.core import queue

    old = datetime.now(UTC) - timedelta(days=40)
    for status, finished in (
        ("done", old),
        ("done", datetime.now(UTC)),
        ("dead", old),
        ("pending", None),
    ):
        await db.execute(
            "INSERT INTO job (id, org_id, job_type, payload, status, finished_at, max_attempts)"
            " VALUES ($1,$2,'t','{}',$3,$4,3)",
            new_id(),
            org_id,
            status,
            finished,
        )
    assert await queue.purge_done(db, 30) == 1
    left = sorted(r["status"] for r in await db.fetch("SELECT status FROM job"))
    assert left == ["dead", "done", "pending"]


async def test_a_paused_schedule_does_not_fire_and_resumes_without_a_restart(db, settings):
    fired: list[str] = []

    async def fn(db, settings):
        fired.append("x")

    scheduler.SCHEDULES.append(scheduler.Schedule("test.pausable", 60, fn))
    try:
        await db.execute(
            "INSERT INTO schedule_state (name, last_run_at, paused) VALUES ($1, now(), true)",
            "test.pausable",
        )
        state: dict[str, float] = {}
        await scheduler.run_scheduler_tick(db, settings, state, 1e12)
        assert fired == []
        await db.execute("UPDATE schedule_state SET paused = false WHERE name = 'test.pausable'")
        await scheduler.run_scheduler_tick(db, settings, state, 1e12)
        assert fired == ["x"]
    finally:
        scheduler.SCHEDULES[:] = [s for s in scheduler.SCHEDULES if s.name != "test.pausable"]
