import json

import httpx
import pytest
import respx

from cs.connectors.fivetran import client

F = "https://api.fivetran.com/v1"


def _settings(settings):
    settings.fivetran_api_key = "key"
    settings.fivetran_api_secret = "secret"
    return settings


@respx.mock
async def test_create_connector_returns_connect_card_url(settings):
    _settings(settings)
    respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "schema": "org1__meta_ads",
                    "connect_card": {"uri": "https://fivetran.com/connect-card/conn_1"},
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        data = await client.create_connector(
            settings,
            http,
            service="facebook_ads",
            group_id="grp_1",
            schema="org1__meta_ads",
            redirect_uri="https://app.example/fivetran-return",
        )
    assert data["id"] == "conn_1"
    assert data["connect_card"]["uri"] == "https://fivetran.com/connect-card/conn_1"
    sent = respx.calls.last.request
    assert sent.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_get_connector(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "schema": "org1__meta_ads"}}
        )
    )
    async with httpx.AsyncClient() as http:
        data = await client.get_connector(settings, http, "conn_1")
    assert data["schema"] == "org1__meta_ads"


@respx.mock
async def test_delete_connector(settings):
    _settings(settings)
    route = respx.delete(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.delete_connector(settings, http, "conn_1")
    assert route.called


@respx.mock
async def test_find_connector_by_schema_matches(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {"id": "conn_1", "schema": "org1__meta_ads"},
                        {"id": "conn_2", "schema": "org2__meta_ads"},
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found == "conn_2"


@respx.mock
async def test_find_connector_by_schema_no_match(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found is None


@respx.mock
async def test_error_response_raises_fivetran_error(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/bad").mock(
        return_value=httpx.Response(400, json={"message": "connector not found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(client.FivetranError, match="connector not found"):
            await client.get_connector(settings, http, "bad")


@respx.mock
async def test_set_paused(settings):
    _settings(settings)
    route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.set_paused(settings, http, "conn_1", False)
    assert json.loads(route.calls.last.request.content) == {"paused": False}


@respx.mock
async def test_lock_down_and_activate_excludes_then_allows_only_required_tables(settings):
    _settings(settings)
    reload_route = respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    for table in client.REQUIRED_TABLES:
        respx.get(f"{F}/connectors/conn_1/schemas/org1__meta_ads/tables/{table}/columns").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"columns": {"id": {"enabled": True}, "spend": {"enabled": False}}}},
            )
        )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    unpause_route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.lock_down_and_activate(settings, http, "conn_1", "org1__meta_ads")

    reload_sent = json.loads(reload_route.calls.last.request.content)
    assert reload_sent == {"exclude_mode": "EXCLUDE"}

    schema_sent = json.loads(schema_route.calls.last.request.content)
    assert schema_sent["schema_change_handling"] == "ALLOW_COLUMNS"
    tables = schema_sent["schemas"]["org1__meta_ads"]["tables"]
    assert set(tables) == set(client.REQUIRED_TABLES)
    assert all(t["enabled"] for t in tables.values())
    assert all(set(t["columns"]) == {"id", "spend"} for t in tables.values())
    assert all(c["enabled"] for t in tables.values() for c in t["columns"].values())

    assert json.loads(unpause_route.calls.last.request.content) == {"paused": False}
