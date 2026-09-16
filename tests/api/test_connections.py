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


@respx.mock
async def test_start_rejects_when_existing_connector_found(client, settings):
    from uuid import UUID

    from cs.connectors.fivetran.connect import schema_for

    settings.fivetran_group_id = "grp_1"
    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    respx.get(f"{F}/groups/grp_1/connectors").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"items": [{"id": "old_conn", "schema": schema_for(UUID(org_id))}]}},
        )
    )
    r = await client.post("/connections/meta/start", headers=H)
    assert r.status_code == 409


async def test_disconnect_requires_admin(client):
    r = await client.post(
        "/connections/meta/disconnect",
        headers=dev_headers(org="org_conn", user="u2", role="member"),
    )
    assert r.status_code == 403


@respx.mock
async def test_disconnect_deletes_connector_and_never_via_start(client, settings):
    from uuid import UUID

    from cs.connectors.fivetran.connect import schema_for

    settings.fivetran_group_id = "grp_1"
    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    respx.get(f"{F}/groups/grp_1/connectors").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"items": [{"id": "old_conn", "schema": schema_for(UUID(org_id))}]}},
        )
    )
    delete_route = respx.delete(f"{F}/connectors/old_conn").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    r = await client.post("/connections/meta/disconnect", headers=H)
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert delete_route.called


@respx.mock
async def test_disconnect_returns_404_when_no_connection_exists(client, settings):
    settings.fivetran_group_id = "grp_1"
    respx.get(f"{F}/groups/grp_1/connectors").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    r = await client.post("/connections/meta/disconnect", headers=H)
    assert r.status_code == 404


async def test_status_reports_disconnected_when_no_connection_exists(client):
    r = await client.get("/connections/meta", headers=dev_headers(org="org_conn_status_none"))
    assert r.status_code == 200
    assert r.json() == {"connected": False, "status": None, "error": None, "last_synced_at": None}


async def test_status_visible_before_any_account_is_discovered(client, db):
    from uuid import UUID

    from cs.core import repo

    org_id = UUID(
        (await client.get("/me", headers=dev_headers(org="org_conn_status_pending"))).json()[
            "org_id"
        ]
    )
    await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    r = await client.get("/connections/meta", headers=dev_headers(org="org_conn_status_pending"))
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["status"] == "pending"
    assert body["error"] is None


async def test_status_surfaces_error_message(client, db):
    from uuid import UUID

    from cs.core import repo

    org_id = UUID(
        (await client.get("/me", headers=dev_headers(org="org_conn_status_error"))).json()["org_id"]
    )
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await repo.set_meta_connection_status(
        db, conn_id, "error", last_error="Disconnected -- reconnect to resume syncing."
    )
    r = await client.get("/connections/meta", headers=dev_headers(org="org_conn_status_error"))
    body = r.json()
    assert body["status"] == "error"
    assert body["error"] == "Disconnected -- reconnect to resume syncing."
