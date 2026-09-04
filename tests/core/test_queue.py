from datetime import UTC, datetime, timedelta

from cs.core import queue


async def test_enqueue_claim_complete(db, org_id):
    jid = await queue.enqueue(db, org_id, "echo", {"x": 1})
    job = await queue.claim(db, "w1")
    assert job is not None and job.id == jid and job.payload == {"x": 1}
    assert await queue.claim(db, "w2") is None  # locked
    await queue.complete(db, jid)
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "done"


async def test_dedup_key_blocks_duplicate_pending(db, org_id):
    a = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    b = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    assert a is not None and b is None
    assert (await queue.claim(db, "w1")).id == a
    await queue.complete(db, a)
    c = await queue.enqueue(db, org_id, "sync", dedup_key="acct-1")
    assert c is not None


async def test_fail_retries_then_dies(db, org_id):
    jid = await queue.enqueue(db, org_id, "flaky", max_attempts=2)
    await queue.claim(db, "w1")
    await queue.fail(db, jid, "boom")
    row = await db.fetchrow("SELECT status, attempts, run_after FROM job WHERE id=$1", jid)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["run_after"] > datetime.now(UTC)
    await db.execute("UPDATE job SET run_after = now() WHERE id=$1", jid)
    await queue.claim(db, "w1")
    await queue.fail(db, jid, "boom again")
    assert await db.fetchval("SELECT status FROM job WHERE id=$1", jid) == "dead"


async def test_run_after_and_job_type_filter(db, org_id):
    later = datetime.now(UTC) + timedelta(hours=1)
    await queue.enqueue(db, org_id, "later", run_after=later)
    await queue.enqueue(db, org_id, "now")
    assert await queue.claim(db, "w", job_types=["later"]) is None
    job = await queue.claim(db, "w", job_types=["now", "later"])
    assert job.job_type == "now"


async def test_reclaim_stale(db, org_id):
    jid = await queue.enqueue(db, org_id, "stuck")
    await queue.claim(db, "w1")
    await db.execute("UPDATE job SET locked_at = now() - interval '1 hour' WHERE id=$1", jid)
    assert await queue.reclaim_stale(db, lease_seconds=600) == 1
    assert (await queue.claim(db, "w2")).id == jid
