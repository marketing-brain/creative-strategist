import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.connectors.fivetran import connect as fivetran_connect
from cs.core.config import Settings
from cs.core.db import Database
from cs.core.queue import enqueue
from cs.ingest import repo

router = APIRouter(prefix="/connections/meta")


class FinalizeBody(BaseModel):
    # Optional at the pydantic layer (with a manual check below) rather than a required
    # field, so a missing connector_id yields a 400 rather than FastAPI's default 422 --
    # matching the explicit contract in tests/api/test_connections.py.
    connector_id: str = ""


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
