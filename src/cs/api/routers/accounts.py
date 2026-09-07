from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core.db import Database
from cs.ingest import repo

router = APIRouter(prefix="/accounts")


@router.get("")
async def list_accounts(
    auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    rows = await repo.list_ad_accounts(db, auth.org_id)
    return [{**dict(r), "id": str(r["id"])} for r in rows]


@router.post("/{account_id}/activate")
async def activate(
    account_id: UUID,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict:
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    await repo.set_ad_account_status(db, auth.org_id, account_id, "active")
    jid = None
    if acct["meta_connection_id"]:
        jid = await queue.enqueue(
            db,
            auth.org_id,
            "meta.sync_from_fivetran",
            {"meta_connection_id": str(acct["meta_connection_id"])},
            dedup_key=str(acct["meta_connection_id"]),
        )
    return {"job_id": str(jid) if jid else None}


@router.post("/{account_id}/sync")
async def sync(
    account_id: UUID,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
) -> dict:
    acct = await repo.get_ad_account(db, auth.org_id, account_id)
    if not acct:
        raise HTTPException(404)
    if not acct["meta_connection_id"]:
        raise HTTPException(409, "account has no Fivetran connection")
    jid = await queue.enqueue(
        db,
        auth.org_id,
        "meta.sync_from_fivetran",
        {"meta_connection_id": str(acct["meta_connection_id"])},
        dedup_key=str(acct["meta_connection_id"]),
    )
    return {"job_id": str(jid) if jid else None}
