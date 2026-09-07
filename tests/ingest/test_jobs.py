import httpx
import respx

from cs.core import queue
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
