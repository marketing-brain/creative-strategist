from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db
from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights import repo
from cs.insights.account_read import ask as account_ask
from cs.insights.account_read import repo as account_read_repo

router = APIRouter()


def _ser(r) -> dict:
    d = dict(r)
    for k, v in d.items():
        if isinstance(v, UUID):
            d[k] = str(v)
        elif hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        elif hasattr(v, "as_integer_ratio") and not isinstance(v, (int, float)):
            d[k] = float(v)
    return d


@router.get("/insights")
async def insights(
    kind: str | None = None,
    account_id: UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    items = [
        _ser(r) for r in await repo.list_insights(db, auth.org_id, kind, account_id, limit, offset)
    ]
    total = await repo.count_insights(db, auth.org_id, kind, account_id)
    return {"items": items, "total": total}


@router.get("/fatigue")
async def fatigue(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    items = [
        _ser(r) for r in await repo.list_insights(db, auth.org_id, "fatigue", None, limit, offset)
    ]
    total = await repo.count_insights(db, auth.org_id, "fatigue", None)
    return {"items": items, "total": total}


@router.get("/briefs")
async def briefs(
    auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    return [_ser(r) for r in await repo.list_briefs(db, auth.org_id)]


@router.get("/briefs/{brief_id}")
async def brief(
    brief_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    r = await db.fetchrow("SELECT * FROM brief WHERE org_id=$1 AND id=$2", auth.org_id, brief_id)
    if not r:
        raise HTTPException(404)
    return _ser(r)


async def _owned_account(db: Database, org_id: UUID, ad_account_id: UUID) -> None:
    owned = await db.fetchval(
        "SELECT 1 FROM ad_account WHERE id=$1 AND org_id=$2", ad_account_id, org_id
    )
    if not owned:
        raise HTTPException(404, "ad account not found")


@router.get("/insights/account-read")
async def account_read(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    await _owned_account(db, auth.org_id, ad_account_id)
    row = await account_read_repo.latest_run(db, auth.org_id, ad_account_id)
    if row is None:
        return {"status": "none"}
    result = row["result"] or {}
    unit_keys = (
        "sha256",
        "headline",
        "spend",
        "creative_ids",
        "rep_creative_id",
        "duration_s",
        "impressions",
        "purchases",
    )
    units = [{k: u.get(k) for k in unit_keys} for u in result.get("units", [])]
    return {
        "status": row["status"],
        "n_units": row["n_units"],
        "created_at": row["created_at"].isoformat(),
        "window_start": row["window_start"].isoformat() if row["window_start"] else None,
        "window_end": row["window_end"].isoformat() if row["window_end"] else None,
        "read": row["read"],
        "summary": result.get("summary", {}),
        "tests": result.get("tests", []),
        "overlaps": result.get("overlaps", []),
        "stratified": result.get("stratified", []),
        "retention_by_reveal": result.get("retention_by_reveal", {}),
        "units": units,
        "error": row["error"],
    }


class AskBody(BaseModel):
    ad_account_id: UUID
    question: str


@router.post("/insights/account-read/ask")
async def account_read_ask(
    body: AskBody,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
) -> dict:
    await _owned_account(db, auth.org_id, body.ad_account_id)
    row = await account_read_repo.latest_run(db, auth.org_id, body.ad_account_id)
    if row is None or row["status"] not in ("done", "rejected"):
        raise HTTPException(409, "no account read run to ask against yet")
    writer = account_ask.ASK_WRITER_FACTORY(get_settings())
    return await account_ask.answer(body.question.strip()[:500], row["result"], writer, draws=300)
