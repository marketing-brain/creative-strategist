from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore
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
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    await _owned_account(db, auth.org_id, ad_account_id)
    row = await account_read_repo.latest_run(db, auth.org_id, ad_account_id)
    if row is None:
        return {"status": "none"}
    page = row["page"] or {}
    units = [dict(u) for u in page.get("units", [])]
    rep_ids = [UUID(u["rep_creative_id"]) for u in units if u.get("rep_creative_id")]
    posters = await account_read_repo.poster_paths(db, auth.org_id, rep_ids)
    for u in units:
        path = posters.get(u.get("rep_creative_id") or "")
        u["poster_url"] = await blobs.url_for(path) if path else None
    return {
        "status": row["status"],
        "n_units": row["n_units"],
        "created_at": row["created_at"].isoformat(),
        "window_start": row["window_start"].isoformat() if row["window_start"] else None,
        "window_end": row["window_end"].isoformat() if row["window_end"] else None,
        "read": row["read"],
        "summary": page.get("summary", {}),
        # Only the tests the read cites (each tagged with its `pid`) and the same attributes
        # on the other funnel metrics. The full list stays in the database.
        "tests": page.get("tests", []),
        "sibling_tests": page.get("sibling_tests", []),
        "units": units,
        "error": row["error"],
        "newer_failed_error": row.get("newer_failed_error"),
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
    result = await account_read_repo.run_result(db, row["id"])
    return await account_ask.answer(body.question.strip()[:500], result, writer, draws=300)
