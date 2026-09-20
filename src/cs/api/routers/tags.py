from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.db import Database
from cs.insights.account_read.facets import DERIVED_FROM_SCENES, REVEAL_BINS, extract
from cs.tagging import repo, vocab
from cs.tagging.taxonomy import load_taxonomy

router = APIRouter()


class BackfillBody(BaseModel):
    limit: int = Field(500, ge=1, le=5000)


@router.get("/creatives/{creative_id}/tags")
async def creative_tags(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    # Tags describe the file, so every copy of it shows the same run.
    ids = await ingest_repo.footage_member_ids(db, auth.org_id, creative_id)
    rows = [dict(r) for r in await repo.footage_latest_tags(db, auth.org_id, ids)]
    derived = _derived_tags(rows)
    # The page states these the way the account patterns judge them. Where a dimension asks
    # the tagger for something the scenes already answer, the derived facet replaces it, so
    # the tags and the patterns beside them can never say different things (`account_read.
    # facets.SKIP_DIMS`). A run without scenes keeps whatever it was asked.
    hidden = {"transcript", *(DERIVED_FROM_SCENES[d["dimension"]] for d in derived)}
    return [
        {**r, "confidence": round(float(r["confidence"]), 4)}
        for r in rows + derived
        if r["dimension"] not in hidden
    ]


def _derived_tags(rows: list[dict]) -> list[dict]:
    """`opening_shows` and `first_product_shown` as the account read computes them, so the
    page and the patterns read one source. Empty when the run has no scenes: absence of a
    scene is not evidence that the product never appears."""
    scenes = [r for r in rows if r["dimension"] == "scenes"]
    if not scenes:
        return []
    x = extract(rows, _duration_s(rows))
    confidence = min(float(r["confidence"]) for r in scenes)
    first = x.first_product_s
    why = {
        "opening_shows": "Read off the scenes that start in the first 3 seconds.",
        "first_product_shown": (
            f"First scene flagged as showing the product or its packaging, at {first:g}s."
            if first is not None
            else "No scene is flagged as showing the product or its packaging."
        ),
    }
    return [
        {
            "dimension": d,
            "kind": "multi" if d == "opening_shows" else "single",
            "value": v,
            "raw_value": None,
            "confidence": confidence,
            "evidence": why[d],
            "t_start_s": 0.0 if d == "opening_shows" else first,
            "ordinal": None,
        }
        for d, v in sorted(x.facets)
        if d in DERIVED_FROM_SCENES
    ]


def _duration_s(rows: list[dict]) -> float | None:
    """`first_product_shown` bins the reveal against the video's length."""
    for r in rows:
        if r["dimension"] == "duration_s":
            try:
                return float(r["value"])
            except (TypeError, ValueError):
                return None
    return None


@router.get("/creatives/{creative_id}/transcript")
async def creative_transcript(
    creative_id: UUID, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> dict:
    if not await ingest_repo.get_creative(db, auth.org_id, creative_id):
        raise HTTPException(404)
    return {"transcript": await repo.latest_tag_value(db, auth.org_id, creative_id, "transcript")}


TAGGABLE_FORMATS = ("image", "video", "carousel")


@router.post("/creatives/{creative_id}/retag")
async def retag(
    creative_id: UUID, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    creative = await ingest_repo.get_creative(db, auth.org_id, creative_id)
    if not creative:
        raise HTTPException(404)
    if creative["format"] not in TAGGABLE_FORMATS:
        # 'unresolved' creatives have no proven format: taxonomy.for_format('unresolved')
        # returns [], which turns into a JSON schema enum of [] -- a request structured-
        # output APIs reject outright. Never enqueue a job doomed to fail before it starts.
        raise HTTPException(422, f"cannot tag a creative with format '{creative['format']}'")
    jid = await queue.enqueue(
        db,
        auth.org_id,
        "tagging.tag_creative",
        {"creative_id": str(creative_id)},
        dedup_key=f"tag:{creative_id}",
    )
    return {"job_id": str(jid) if jid else None}


@router.get("/tags/dimensions")
async def dimensions(auth: AuthContext = Depends(current_auth)) -> list[dict]:
    t = load_taxonomy()
    return [
        {
            "name": d.name,
            "group": d.group,
            "applies_to": list(d.applies_to),
            "values": list(d.values) if d.values else None,
            "description": d.description,
            "kind": d.kind,
        }
        for d in t.dimensions
    ] + [
        {
            "name": "opening_shows",
            "group": "structure",
            "applies_to": ["video"],
            "values": ["face", "product"],
            "description": "What the scenes starting in the first 3 seconds show. Read off "
            "the scene flags, not asked of the tagger.",
            "kind": "multi",
        },
        {
            "name": "first_product_shown",
            "group": "structure",
            "applies_to": ["video"],
            "values": list(REVEAL_BINS),
            "description": "When the product or its packaging first appears, binned against "
            "the video's length. Read off the scene flags, not asked of the tagger.",
            "kind": "single",
        },
    ]


@router.get("/tags/dimensions/{name}/values")
async def dimension_values(
    name: str, auth: AuthContext = Depends(current_auth), db: Database = Depends(get_db)
) -> list[dict]:
    dim = next((d for d in load_taxonomy().dimensions if d.name == name), None)
    if dim is None or dim.kind != "open":
        raise HTTPException(404, "not an open dimension")
    return [
        {"value": slug, "count": n} for slug, n in await vocab.hints(db, auth.org_id, name, 200)
    ]


@router.post("/tagging/backfill")
async def backfill(
    body: BackfillBody, auth: AuthContext = Depends(require_admin), db: Database = Depends(get_db)
) -> dict:
    jid = await queue.enqueue(
        db, auth.org_id, "tagging.batch_submit", {"limit": body.limit}, dedup_key="batch_submit"
    )
    return {"job_id": str(jid) if jid else None}
