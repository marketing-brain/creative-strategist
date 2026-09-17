import asyncio

import pytest

from cs.core import queue
from cs.jobs import registry, worker
from cs.jobs.worker import _keep_lease, run_once


async def test_run_once_executes_handler_and_completes(db, blobs, settings, org_id):
    seen = []

    @registry.handler("test.echo")
    async def echo(ctx: registry.JobContext) -> None:
        seen.append(ctx.job.payload["msg"])

    jid = await queue.enqueue(db, org_id, "test.echo", {"msg": "hi"})
    assert await run_once(db, blobs, settings, "w1") is True
    assert seen == ["hi"]
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "done"
    assert await run_once(db, blobs, settings, "w1") is False


async def test_run_once_marks_failure(db, blobs, settings, org_id):
    @registry.handler("test.boom")
    async def boom(ctx: registry.JobContext) -> None:
        raise ValueError("nope")

    jid = await queue.enqueue(db, org_id, "test.boom", max_attempts=1)
    await run_once(db, blobs, settings, "w1")
    row = await db.fetchrow("SELECT status, error FROM job WHERE id=$1", jid)
    assert row["status"] == "dead" and "nope" in row["error"]


async def test_unknown_job_type_is_failed_not_crashed(db, blobs, settings, org_id):
    jid = await queue.enqueue(db, org_id, "test.unknown", max_attempts=1)
    await run_once(db, blobs, settings, "w1")
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "dead"


async def test_keep_lease_extends_a_held_lease(db, org_id):
    jid = await queue.enqueue(db, org_id, "test.slow")
    job = await queue.claim(db, "w1")
    await db.execute("UPDATE job SET locked_at = now() - interval '5 minutes' WHERE id=$1", jid)
    before = await db.fetchval("SELECT locked_at FROM job WHERE id=$1", jid)
    handler = asyncio.create_task(asyncio.sleep(5))
    lease = asyncio.create_task(_keep_lease(db, job, "w1", 0.01, handler))
    await asyncio.sleep(0.05)
    lease.cancel()
    handler.cancel()
    assert await db.fetchval("SELECT locked_at FROM job WHERE id=$1", jid) > before


async def test_keep_lease_cancels_the_handler_when_the_lease_is_lost(db, org_id):
    jid = await queue.enqueue(db, org_id, "test.slow")
    job = await queue.claim(db, "w1")
    await db.execute("UPDATE job SET locked_by='w2' WHERE id=$1", jid)
    handler = asyncio.create_task(asyncio.sleep(5))
    assert await _keep_lease(db, job, "w1", 0.01, handler) is False
    with pytest.raises(asyncio.CancelledError):
        await handler


class _Stop(BaseException):
    """Breaks out of run_worker's infinite loop without tripping its guard."""


async def test_complete_failure_propagates_instead_of_failing_the_job(
    db, blobs, settings, org_id, monkeypatch
):
    @registry.handler("test.ok")
    async def ok(ctx: registry.JobContext) -> None:
        return None

    async def boom_complete(*args, **kwargs):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(worker.queue, "complete", boom_complete)
    jid = await queue.enqueue(db, org_id, "test.ok")
    with pytest.raises(RuntimeError, match="connection reset"):
        await run_once(db, blobs, settings, "w1")
    row = await db.fetchrow("SELECT status, error FROM job WHERE id=$1", jid)
    assert row["status"] == "running" and row["error"] is None


async def test_run_worker_backs_off_and_survives_run_once_failures(blobs, settings, monkeypatch):
    class _StubDB:
        @classmethod
        async def connect(cls, url):
            return cls()

        async def close(self):
            return None

    calls: list[int] = []
    sleeps: list[float] = []

    async def flaky_run_once(*args, **kwargs):
        calls.append(1)
        if len(calls) <= 3:
            raise RuntimeError("db is down")
        raise _Stop

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(worker, "Database", _StubDB)
    monkeypatch.setattr(worker, "blob_store_from_settings", lambda s: blobs)
    monkeypatch.setattr(worker, "run_once", flaky_run_once)
    monkeypatch.setattr(worker.asyncio, "sleep", fake_sleep)

    with pytest.raises(_Stop):
        await worker.run_worker(settings)
    assert len(calls) == 4
    assert sleeps == [2, 4, 8]


async def test_run_worker_runs_up_to_worker_concurrency_jobs_at_once(
    db, blobs, settings, org_id, monkeypatch
):
    """Two blocking jobs both finish only if the worker runs them side by side."""
    started = 0
    both_started = asyncio.Event()

    @registry.handler("test.pair")
    async def pair(ctx: registry.JobContext) -> None:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=3)

    ids = [await queue.enqueue(db, org_id, "test.pair", max_attempts=1) for _ in range(2)]
    monkeypatch.setattr(worker, "blob_store_from_settings", lambda s: blobs)
    concurrent = settings.model_copy(
        update={"worker_concurrency": 2, "database_url": settings.database_url_test}
    )

    run = asyncio.create_task(worker.run_worker(concurrent))
    try:
        for _ in range(100):
            statuses = await db.fetch("SELECT status FROM job WHERE id = ANY($1)", ids)
            if all(r["status"] in ("done", "dead") for r in statuses):
                break
            await asyncio.sleep(0.1)
    finally:
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

    statuses = await db.fetch("SELECT status FROM job WHERE id = ANY($1)", ids)
    assert [r["status"] for r in statuses] == ["done", "done"]
