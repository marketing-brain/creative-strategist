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
