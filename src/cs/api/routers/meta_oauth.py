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


def _sign_state(settings: Settings, org_id: str) -> str:
    return jwt.encode({"org_id": org_id}, settings.token_encryption_key, algorithm="HS256")


def _read_state(settings: Settings, state: str) -> str:
    try:
        return jwt.decode(state, settings.token_encryption_key, algorithms=["HS256"])["org_id"]
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
    async with httpx.AsyncClient(timeout=30) as http:
        token, expires = await oauth.exchange_code(settings, code, http)
        accounts = await oauth.list_ad_accounts(settings, token, http)
    enc = encrypt_str(settings, token)
    stored = []
    for a in accounts:
        aid = await repo.upsert_ad_account(
            db, org_id, a["account_id"], a["name"], a["currency"], enc, expires
        )
        stored.append({"id": str(aid), **a})
    return {"accounts": stored}
