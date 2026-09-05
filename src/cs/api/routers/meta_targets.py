from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db, get_settings_dep
from cs.core.blobs import BlobStore
from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest import repo, service

router = APIRouter(prefix="/accounts")


async def _connector_and_account(db, settings, blobs, auth, account_id, http):
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    return service.CONNECTOR_FACTORY(settings, acct, http, blobs), acct


@router.get("/{account_id}/campaigns")
async def campaigns(account_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                    settings: Settings = Depends(get_settings_dep), blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as http:
        c, acct = await _connector_and_account(db, settings, blobs, auth, account_id, http)
        return await c.list_campaigns(acct["platform_account_id"])


@router.get("/{account_id}/adsets")
async def adsets(account_id: UUID, campaign_id: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db),
                 settings: Settings = Depends(get_settings_dep), blobs: BlobStore = Depends(get_blobs)) -> list[dict]:
    async with httpx.AsyncClient(timeout=60) as http:
        c, acct = await _connector_and_account(db, settings, blobs, auth, account_id, http)
        return await c.list_adsets(acct["platform_account_id"], campaign_id)
