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
            settings, http, service="facebook_ads", group_id="grp_1",
            schema="org1__meta_ads", redirect_uri="https://app.example/fivetran-return",
        )
    assert data["id"] == "conn_1"
    assert data["connect_card"]["uri"] == "https://fivetran.com/connect-card/conn_1"
    sent = respx.calls.last.request
    assert sent.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_get_connector(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1", "schema": "org1__meta_ads"}})
    )
    async with httpx.AsyncClient() as http:
        data = await client.get_connector(settings, http, "conn_1")
    assert data["schema"] == "org1__meta_ads"


@respx.mock
async def test_delete_connector(settings):
    _settings(settings)
    route = respx.delete(f"{F}/connectors/conn_1").mock(return_value=httpx.Response(200, json={"data": {}}))
    async with httpx.AsyncClient() as http:
        await client.delete_connector(settings, http, "conn_1")
    assert route.called


@respx.mock
async def test_find_connector_by_schema_matches(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"items": [
                {"id": "conn_1", "schema": "org1__meta_ads"},
                {"id": "conn_2", "schema": "org2__meta_ads"},
            ]}},
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
