import httpx
import respx

from cs.core import queue
from cs.ingest import jobs as ingest_jobs
from cs.ingest import repo
from cs.jobs.worker import run_once

F = "https://api.fivetran.com/v1"


@respx.mock
async def test_await_first_sync_reraises_until_fivetran_finishes(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "status": {"setup_state": "connected"}}}
        )
    )
    await queue.enqueue(
        db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)}, max_attempts=3
    )
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "pending"
    assert await db.fetchval("SELECT status FROM job") == "pending"  # retried, not dead yet


@respx.mock
async def test_await_first_sync_enqueues_sync_once_fivetran_succeeds(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "status": {"setup_state": "connected"},
                    "succeeded_at": "2026-09-01T00:00:00Z",
                }
            },
        )
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "ready"
    job_type = await db.fetchval("SELECT job_type FROM job WHERE status='pending'")
    assert job_type == "meta.sync_from_fivetran"


@respx.mock
async def test_await_first_sync_marks_broken_connector_as_error(db, blobs, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "status": {"setup_state": "broken"}}}
        )
    )
    await queue.enqueue(db, org_id, "meta.await_first_sync", {"meta_connection_id": str(conn_id)})
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.get_meta_connection_by_id(db, conn_id)
    assert row["sync_status"] == "error" and "reconnect" in row["last_error"].lower()


async def test_nightly_sync_enqueues_sync_for_ready_connections_with_active_account(
    db, settings, org_id
):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await repo.set_meta_connection_status(db, conn_id, "ready")
    acct_id = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)
    await repo.set_ad_account_status(db, org_id, acct_id, "active")

    await ingest_jobs.nightly_sync(db, settings)

    job = await db.fetchrow(
        "SELECT * FROM job WHERE job_type='meta.sync_from_fivetran' AND org_id=$1", org_id
    )
    assert job is not None
    assert job["payload"]["meta_connection_id"] == str(conn_id)


async def test_nightly_sync_skips_connections_without_an_active_account(db, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await repo.set_meta_connection_status(db, conn_id, "ready")
    await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)  # stays 'paused'

    await ingest_jobs.nightly_sync(db, settings)

    assert await db.fetchval("SELECT count(*) FROM job WHERE org_id=$1", org_id) == 0


async def test_nightly_sync_skips_non_ready_connections(db, settings, org_id):
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")  # stays 'pending'
    acct_id = await repo.upsert_ad_account(db, org_id, "999", "Main", "USD", conn_id)
    await repo.set_ad_account_status(db, org_id, acct_id, "active")

    await ingest_jobs.nightly_sync(db, settings)

    assert await db.fetchval("SELECT count(*) FROM job WHERE org_id=$1", org_id) == 0
