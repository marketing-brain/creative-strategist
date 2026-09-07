import httpx
import pytest
import respx

from cs.connectors.meta import oauth


def _settings(settings):
    settings.meta_app_id = "app1"
    settings.meta_app_secret = "sec"
    settings.meta_redirect_uri = "http://localhost:8000/meta/oauth/callback"
    return settings


def test_authorize_url(settings):
    url = oauth.authorize_url(_settings(settings), state="abc")
    assert url.startswith("https://www.facebook.com/v21.0/dialog/oauth?")
    assert "client_id=app1" in url and "state=abc" in url
    assert "scope=ads_read" in url


@respx.mock
async def test_exchange_code_returns_long_lived_token(settings):
    s = _settings(settings)
    route = respx.post("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "short"}),
            httpx.Response(200, json={"access_token": "long", "expires_in": 5184000}),
        ]
    )
    async with httpx.AsyncClient() as http:
        token, expires = await oauth.exchange_code(s, "code123", http)
    assert token == "long" and expires is not None
    for call in route.calls:
        assert "client_secret" not in str(call.request.url)
        assert b"client_secret=sec" in call.request.content


@respx.mock
async def test_exchange_code_error_hides_the_app_secret(settings, monkeypatch):
    s = _settings(settings)
    monkeypatch.setattr(s, "meta_app_secret", "supersecretvalue")
    respx.post("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "code expired", "code": 100}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(oauth.OAuthError) as excinfo:
            await oauth.exchange_code(s, "code123", http)
    message = str(excinfo.value)
    assert "400" in message and "code expired" in message
    assert "client_secret" not in message and "supersecretvalue" not in message
    assert "graph.facebook.com" not in message


@respx.mock
async def test_list_ad_accounts(settings):
    route = respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [{"id": "act_123", "account_id": "123", "name": "Main", "currency": "USD"}]
            },
        )
    )
    async with httpx.AsyncClient() as http:
        accts = await oauth.list_ad_accounts(_settings(settings), "tok", http)
    assert accts == [{"account_id": "123", "name": "Main", "currency": "USD"}]
    request = route.calls[0].request
    assert request.headers["authorization"] == "Bearer tok"
    assert "access_token=" not in str(request.url)


@respx.mock
async def test_list_ad_accounts_error_hides_the_token(settings):
    respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
        return_value=httpx.Response(401, json={"error": {"message": "token expired"}})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(oauth.OAuthError) as excinfo:
            await oauth.list_ad_accounts(_settings(settings), "sekrit", http)
    assert "sekrit" not in str(excinfo.value) and "401" in str(excinfo.value)
