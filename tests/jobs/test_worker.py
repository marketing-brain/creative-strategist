from cs.core import queue
from cs.jobs import registry
from cs.jobs.worker import run_once


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
