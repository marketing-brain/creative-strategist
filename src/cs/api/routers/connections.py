import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.connectors.fivetran import connect as fivetran_connect
from cs.core import repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.queue import enqueue
from cs.ingest.sync_status import sync_status

router = APIRouter(prefix="/connections/meta")


class FinalizeBody(BaseModel):
    # Optional at the pydantic layer (with a manual check below) rather than a required
    # field, so a missing connector_id yields a 400 rather than FastAPI's default 422 --
    # matching the explicit contract in tests/api/test_connections.py.
    connector_id: str = ""


@router.get("")
async def status(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    """Connection status independent of ad_account rows -- a meta_connection can exist
    (mid first-sync, or errored with zero accounts ever discovered) while /accounts
    returns nothing, which used to leave the UI with no signal that a connection was
    already in progress."""
    conn = await repo.get_meta_connection(db, auth.org_id)
    if conn is None:
        return {"connected": False, "status": None, "error": None, "last_synced_at": None}
    return {
        "connected": True,
        "status": conn["sync_status"],
        "error": conn["last_error"],
        "last_synced_at": (conn["last_synced_at"].isoformat() if conn["last_synced_at"] else None),
    }


@router.get("/sync")
async def sync_state(
    auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    return await sync_status(db, auth.org_id)


@router.post("/sync")
async def sync_now(
    auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    """One sync covers every activated account on the connection, so it hangs off the
    connection, not an account. A sync already queued or running is returned, not doubled."""
    conn = await repo.get_meta_connection(db, auth.org_id)
    if conn is None or conn["sync_status"] in ("pending", "error"):
        raise HTTPException(409, "the Meta connection is not ready to sync")
    await enqueue(
        db,
        auth.org_id,
        "meta.sync_from_fivetran",
        {"meta_connection_id": str(conn["id"])},
        dedup_key=str(conn["id"]),
    )
    return await sync_status(db, auth.org_id)


@router.post("/start")
async def start(
    auth: AuthContext = Depends(require_admin), settings: Settings = Depends(get_settings_dep)
) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            return await fivetran_connect.start_connection(settings, http, auth.org_id)
        except fivetran_connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc)) from None


@router.post("/finalize")
async def finalize(
    body: FinalizeBody,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    if not body.connector_id:
        raise HTTPException(400, "connector_id required")
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            await fivetran_connect.finalize_connection(
                settings, http, auth.org_id, body.connector_id
            )
        except fivetran_connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc)) from None
    schema = fivetran_connect.schema_for(auth.org_id)
    conn_id = await repo.upsert_meta_connection(db, auth.org_id, body.connector_id, schema)
    await enqueue(
        db,
        auth.org_id,
        "meta.await_first_sync",
        {"meta_connection_id": str(conn_id)},
        dedup_key=str(conn_id),
        max_attempts=300,  # ~24h ceiling: exponential backoff caps at 5 minutes after ~9 attempts
    )
    return {"ok": True}


@router.post("/disconnect")
async def disconnect(
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    async with httpx.AsyncClient(timeout=30) as http:
        try:
            await fivetran_connect.disconnect_connection(settings, http, auth.org_id)
        except fivetran_connect.ConnectError as exc:
            raise HTTPException(exc.status, str(exc)) from None
    conn = await repo.get_meta_connection(db, auth.org_id)
    if conn:
        await repo.set_meta_connection_status(
            db, conn["id"], "error", last_error="Disconnected -- reconnect to resume syncing."
        )
    return {"ok": True}
