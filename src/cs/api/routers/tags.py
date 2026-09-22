from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from cs.api.auth import AuthContext, current_auth, require_admin
from cs.api.deps import get_db, get_settings_dep
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.insights.account_read.facets import DERIVED_FROM_SCENES, REVEAL_BINS, extract
from cs.tagging import repo, vocab
from cs.tagging.gates import TAG_JOB, explain, tag_blocker
from cs.tagging.taxonomy import load_taxonomy

router = APIRouter()


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


@router.post("/creatives/{creative_id}/retag")
async def retag(
    creative_id: UUID,
    force: bool = False,
    auth: AuthContext = Depends(require_admin),
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> dict:
    """Queue a tag run. The gate (cs.tagging.gates) refuses a creative the sync would not
    have tagged either -- no file, no proven format, too little delivery to compare, or the
    file's tag budget already spent -- and says which, so the caller is never left guessing.
    `force=true` is an admin paying for it anyway, which is how a file gets tagged a second
    time to measure how far the tagger agrees with itself."""
    creative = await ingest_repo.get_creative(db, auth.org_id, creative_id)
    if not creative:
        raise HTTPException(404)
    if not force:
        blocker, facts = await tag_blocker(db, settings, auth.org_id, creative)
        if blocker is not None:
            raise HTTPException(422, {"reason": blocker, "detail": explain(blocker, facts)})
    jid = await queue.enqueue(
        db,
        auth.org_id,
        TAG_JOB,
        {"creative_id": str(creative_id), "force": force},
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
