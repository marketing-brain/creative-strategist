import httpx
import respx

from tests.conftest import dev_headers


async def test_start_returns_url(client):
    r = await client.get("/meta/oauth/start", headers=dev_headers())
    assert r.status_code == 200 and "dialog/oauth" in r.json()["url"]


@respx.mock
async def test_callback_stores_paused_accounts(client, db):
    start = await client.get("/meta/oauth/start", headers=dev_headers())
    state = start.json()["state"]
    respx.get("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "s"}),
            httpx.Response(200, json={"access_token": "l", "expires_in": 100}),
        ]
    )
    respx.get("https://graph.facebook.com/v21.0/me/adaccounts").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "act_1", "account_id": "1", "name": "A", "currency": "USD"}]}
        )
    )
    r = await client.get(f"/meta/oauth/callback?code=c&state={state}")
    assert r.status_code == 200
    row = await db.fetchrow(
        "SELECT status, access_token_enc FROM ad_account WHERE platform_account_id='1'"
    )
    assert row["status"] == "paused" and row["access_token_enc"] != "l"
