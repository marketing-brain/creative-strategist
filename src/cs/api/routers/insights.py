from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_db
from cs.core.db import Database
from cs.insights import repo

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
async def insights(kind: str | None = None, account_id: UUID | None = None, limit: int = 50,
                   auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_insights(db, auth.org_id, kind, account_id, min(limit, 200))]


@router.get("/fatigue")
async def fatigue(limit: int = 50, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_insights(db, auth.org_id, "fatigue", None, min(limit, 200))]


@router.get("/briefs")
async def briefs(auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> list[dict]:
    return [_ser(r) for r in await repo.list_briefs(db, auth.org_id)]


@router.get("/briefs/{brief_id}")
async def brief(brief_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)) -> dict:
    r = await db.fetchrow("SELECT * FROM brief WHERE org_id=$1 AND id=$2", auth.org_id, brief_id)
    if not r:
        raise HTTPException(404)
    return _ser(r)
