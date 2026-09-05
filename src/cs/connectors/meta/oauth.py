from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx

from cs.core.config import Settings

SCOPES = "ads_read,ads_management,business_management"


def _graph(settings: Settings) -> str:
    return f"https://graph.facebook.com/{settings.meta_api_version}"


def authorize_url(settings: Settings, state: str) -> str:
    q = urlencode(
        {
            "client_id": settings.meta_app_id,
            "redirect_uri": settings.meta_redirect_uri,
            "scope": SCOPES,
            "response_type": "code",
            "state": state,
        }
    )
    return f"https://www.facebook.com/{settings.meta_api_version}/dialog/oauth?{q}"


async def exchange_code(
    settings: Settings, code: str, http: httpx.AsyncClient
) -> tuple[str, datetime | None]:
    r = await http.get(
        f"{_graph(settings)}/oauth/access_token",
        params={
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "redirect_uri": settings.meta_redirect_uri,
            "code": code,
        },
    )
    r.raise_for_status()
    short = r.json()["access_token"]
    r = await http.get(
        f"{_graph(settings)}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "fb_exchange_token": short,
        },
    )
    r.raise_for_status()
    body = r.json()
    expires = None
    if body.get("expires_in"):
        expires = datetime.now(UTC) + timedelta(seconds=int(body["expires_in"]))
    return body["access_token"], expires


async def list_ad_accounts(settings: Settings, token: str, http: httpx.AsyncClient) -> list[dict]:
    r = await http.get(
        f"{_graph(settings)}/me/adaccounts",
        params={"fields": "id,account_id,name,currency", "limit": 200, "access_token": token},
    )
    r.raise_for_status()
    return [
        {"account_id": a["account_id"], "name": a["name"], "currency": a.get("currency", "USD")}
        for a in r.json().get("data", [])
    ]
