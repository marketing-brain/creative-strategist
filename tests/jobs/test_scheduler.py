import time

import pytest

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
