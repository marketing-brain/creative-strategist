from datetime import UTC, datetime, timedelta

import httpx
import jwt
import respx

from cs.api.routers import meta_oauth
from cs.core.ids import new_id
from tests.conftest import dev_headers


async def test_start_returns_url(client):
    r = await client.get("/meta/oauth/start", headers=dev_headers())
    assert r.status_code == 200 and "dialog/oauth" in r.json()["url"]


@respx.mock
async def test_callback_stores_paused_accounts(client, db):
    start = await client.get("/meta/oauth/start", headers=dev_headers())
    state = start.json()["state"]
    respx.post("https://graph.facebook.com/v21.0/oauth/access_token").mock(
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


async def test_callback_rejects_expired_state(client, settings):
    issued = datetime.now(UTC) - timedelta(minutes=30)
    state = jwt.encode(
        {"org_id": str(new_id()), "iat": issued, "exp": issued + meta_oauth.STATE_TTL},
        meta_oauth._state_secret(settings),
        algorithm="HS256",
    )
    r = await client.get(f"/meta/oauth/callback?code=c&state={state}")
    assert r.status_code == 400 and r.json()["detail"] == "bad state"


async def test_callback_rejects_state_signed_with_the_raw_key(client, settings):
    state = jwt.encode(
        {"org_id": str(new_id()), "exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.token_encryption_key,
        algorithm="HS256",
    )
    r = await client.get(f"/meta/oauth/callback?code=c&state={state}")
    assert r.status_code == 400


@respx.mock
async def test_callback_returns_502_without_leaking_the_secret(client):
    start = await client.get("/meta/oauth/start", headers=dev_headers())
    state = start.json()["state"]
    respx.post("https://graph.facebook.com/v21.0/oauth/access_token").mock(
        return_value=httpx.Response(400, json={"error": {"message": "code expired"}})
    )
    r = await client.get(f"/meta/oauth/callback?code=c&state={state}")
    assert r.status_code == 502 and r.json()["detail"] == "meta oauth exchange failed"
    assert "client_secret" not in r.text
