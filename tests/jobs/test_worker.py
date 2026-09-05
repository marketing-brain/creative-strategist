import asyncio

import pytest

from cs.core import queue
from cs.jobs import registry
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
