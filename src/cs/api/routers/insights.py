from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cs.api.auth import AuthContext, current_auth
from cs.api.deps import get_blobs, get_db
from cs.core.blobs import BlobStore
from cs.core.config import get_settings
from cs.core.db import Database
from cs.insights.account_read import ask as account_ask
from cs.insights.account_read import repo as account_read_repo
from cs.insights.next_plan import repo as next_plan_repo

router = APIRouter()


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
        "coverage": await account_read_repo.coverage(db, auth.org_id, ad_account_id),
        "error": row["error"],
        "newer_failed_error": row.get("newer_failed_error"),
    }


@router.get("/insights/next")
async def next_plan(
    ad_account_id: UUID,
    auth: AuthContext = Depends(current_auth),
    db: Database = Depends(get_db),
    blobs: BlobStore = Depends(get_blobs),
) -> dict:
    """The stored plan, whole. Unlike the account read there is no page slice: the payload is
    bounded by MAX_PER_KIND, MAX_COMBOS and the explore list, so it is a few kilobytes, and the
    job that wrote it did all the querying (see docs/next-surface.md §6)."""
    await _owned_account(db, auth.org_id, ad_account_id)
    row = await next_plan_repo.latest_run(db, auth.org_id, ad_account_id)
    if row is None:
        return {"status": "none"}
    plan = dict(row["plan"] or {})
    await _attach_posters(db, blobs, auth.org_id, plan)
    return {
        "status": row["status"],
        "created_at": row["created_at"].isoformat(),
        "error": row["error"],
        **plan,
    }


async def _attach_posters(db: Database, blobs: BlobStore, org_id: UUID, plan: dict) -> None:
    """One poster lookup for every creative the plan names, allocation rows and brief
    references alike -- a row per query would be a dozen round trips for one page."""
    holders = [
        *(plan.get("allocate") or []),
        *(r for b in (plan.get("make") or []) for r in (b.get("refs") or [])),
    ]
    # Set on every holder, including when nothing resolves: a row that omits the key entirely
    # makes the page tell "no poster" apart from "field absent", which is the same thing here.
    for h in holders:
        h["poster_url"] = None
    ids = {h["rep_creative_id"] for h in holders if h.get("rep_creative_id")}
    if not ids:
        return
    posters = await account_read_repo.poster_paths(db, org_id, [UUID(i) for i in ids])
    for h in holders:
        path = posters.get(h.get("rep_creative_id") or "")
        if path:
            h["poster_url"] = await blobs.url_for(path)


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
