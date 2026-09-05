import hashlib
import hmac
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.connectors.meta import oauth
from cs.core.config import Settings
from cs.core.crypto import encrypt_str
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/meta/oauth")
log = logging.getLogger("cs.api")

STATE_TTL = timedelta(minutes=10)


def _state_secret(settings: Settings) -> str:
    """Derive a signing key so the state JWT never uses the blob encryption key directly."""
    return hmac.new(
        settings.token_encryption_key.encode(), b"meta-oauth-state", hashlib.sha256
    ).hexdigest()


def _sign_state(settings: Settings, org_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {"org_id": org_id, "iat": now, "exp": now + STATE_TTL},
        _state_secret(settings),
        algorithm="HS256",
    )


def _read_state(settings: Settings, state: str) -> str:
    try:
        return jwt.decode(
            state,
            _state_secret(settings),
            algorithms=["HS256"],
            options={"require": ["exp", "org_id"]},
        )["org_id"]
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "bad state") from exc


@router.get("/start")
async def start(
    auth: AuthContext = Depends(require_admin), settings: Settings = Depends(get_settings_dep)
) -> dict:
    state = _sign_state(settings, str(auth.org_id))
    return {"url": oauth.authorize_url(settings, state), "state": state}


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    org_id = UUID(_read_state(settings, state))
    try:
        async with httpx.AsyncClient(timeout=30) as http:
            token, expires = await oauth.exchange_code(settings, code, http)
            accounts = await oauth.list_ad_accounts(settings, token, http)
    except oauth.OAuthError as exc:
        log.warning("meta oauth exchange failed", extra={"org_id": str(org_id), "reason": str(exc)})
        raise HTTPException(502, "meta oauth exchange failed") from None
    enc = encrypt_str(settings, token)
    stored = []
    for a in accounts:
        aid = await repo.upsert_ad_account(
            db, org_id, a["account_id"], a["name"], a["currency"], enc, expires
        )
        stored.append({"id": str(aid), **a})
    return {"accounts": stored}
