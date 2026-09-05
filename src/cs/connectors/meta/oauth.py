from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import NoReturn
from urllib.parse import urlencode

import httpx

from cs.core.config import Settings

SCOPES = "ads_read,ads_management,business_management"


class OAuthError(RuntimeError):
    """A Meta OAuth call failed. The message never carries the request URL or credentials."""


def _graph(settings: Settings) -> str:
    return f"https://graph.facebook.com/{settings.meta_api_version}"


def _fail(exc: httpx.HTTPStatusError) -> NoReturn:
    """Re-raise as an OAuthError carrying only the status and Meta's error message.

    httpx's own message embeds the request URL, which for these calls would leak the app
    secret or the access token into logs and API responses.
    """
    try:
        message = ((exc.response.json() or {}).get("error") or {}).get("message", "")
    except (ValueError, AttributeError):
        message = ""
    raise OAuthError(f"meta oauth failed with {exc.response.status_code}: {message}") from None


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
    # POST form bodies, not query params: the app secret must never appear in a URL.
    try:
        r = await http.post(
            f"{_graph(settings)}/oauth/access_token",
            data={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": settings.meta_redirect_uri,
                "code": code,
            },
        )
        r.raise_for_status()
        short = r.json()["access_token"]
        r = await http.post(
            f"{_graph(settings)}/oauth/access_token",
            data={
                "grant_type": "fb_exchange_token",
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "fb_exchange_token": short,
            },
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _fail(exc)
    body = r.json()
    expires = None
    if body.get("expires_in"):
        expires = datetime.now(UTC) + timedelta(seconds=int(body["expires_in"]))
    return body["access_token"], expires


async def list_ad_accounts(settings: Settings, token: str, http: httpx.AsyncClient) -> list[dict]:
    try:
        r = await http.get(
            f"{_graph(settings)}/me/adaccounts",
            params={"fields": "id,account_id,name,currency", "limit": 200},
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        _fail(exc)
    return [
        {"account_id": a["account_id"], "name": a["name"], "currency": a.get("currency", "USD")}
        for a in r.json().get("data", [])
    ]
