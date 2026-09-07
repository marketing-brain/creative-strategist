import httpx
import respx

from tests.conftest import dev_headers

H = dev_headers(org="org_conn")
F = "https://api.fivetran.com/v1"


async def test_start_requires_admin(client):
    r = await client.post(
        "/connections/meta/start", headers=dev_headers(org="org_conn", user="u2", role="member")
    )
    assert r.status_code == 403


@respx.mock
async def test_start_returns_connect_card_url(client, settings):
    settings.fivetran_group_id = "grp_1"
    respx.get(f"{F}/groups/grp_1/connectors").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"id": "conn_1", "schema": "x", "connect_card": {"uri": "https://card"}}},
        )
    )
    r = await client.post("/connections/meta/start", headers=H)
    assert r.status_code == 200
    assert r.json() == {"connect_card_url": "https://card", "connector_id": "conn_1"}


@respx.mock
async def test_finalize_persists_connection_and_enqueues_await_job(client, db):
    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    from uuid import UUID

    from cs.connectors.fivetran.connect import schema_for

    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "schema": schema_for(UUID(org_id))}}
        )
    )
    r = await client.post("/connections/meta/finalize", headers=H, json={"connector_id": "conn_1"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = await db.fetchrow("SELECT * FROM meta_connection WHERE org_id=$1", UUID(org_id))
    assert row["fivetran_connector_id"] == "conn_1" and row["sync_status"] == "pending"
    assert await db.fetchval("SELECT job_type FROM job") == "meta.await_first_sync"


@respx.mock
async def test_finalize_rejects_connector_from_another_schema(client):
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "schema": "not_yours__meta_ads"}}
        )
    )
    r = await client.post("/connections/meta/finalize", headers=H, json={"connector_id": "conn_1"})
    assert r.status_code == 403


async def test_finalize_requires_connector_id(client):
    r = await client.post("/connections/meta/finalize", headers=H, json={})
    assert r.status_code == 400
