import httpx
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
    assert "scope=ads_read%2Cads_management%2Cbusiness_management" in url


@respx.mock
async def test_exchange_code_returns_long_lived_token(settings):
    s = _settings(settings)
    respx.get("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "short"}),
            httpx.Response(200, json={"access_token": "long", "expires_in": 5184000}),
        ]
    )
    async with httpx.AsyncClient() as http:
        token, expires = await oauth.exchange_code(s, "code123", http)
    assert token == "long" and expires is not None


@respx.mock
async def test_list_ad_accounts(settings):
    respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
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
