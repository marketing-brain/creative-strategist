from cs.connectors.base import PlatformCreative
from cs.core import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy


async def test_finish_tag_run_is_idempotent_on_retry(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    await repo.finish_tag_run(db, org_id, rid, tags, "sha", 1, 1)
    await repo.finish_tag_run(
        db, org_id, rid, tags, "sha", 1, 1
    )  # simulates a reclaimed/retried job
    rows = await db.fetch("SELECT dimension, value FROM tag WHERE tag_run_id=$1", rid)
    assert len(rows) == 1


async def test_finish_tag_run_round_trips_v2_columns(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("v", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    rows = [
        {
            "dimension": "messenger",
            "kind": "single",
            "value": "expert_on_camera",
            "confidence": 0.9,
            "evidence": "coat",
        },
        {
            "dimension": "beats",
            "kind": "ordered",
            "value": "problem",
            "confidence": 1.0,
            "evidence": "",
            "t_start_s": 5.0,
            "ordinal": 1,
        },
        {
            "dimension": "beats",
            "kind": "ordered",
            "value": "hook",
            "confidence": 1.0,
            "evidence": "",
            "t_start_s": 0.0,
            "ordinal": 0,
        },
        {
            "dimension": "concept_tags",
            "kind": "open",
            "value": "pharmacist_warning",
            "raw_value": "Pharmacist Warning",
            "confidence": 1.0,
            "evidence": "e",
        },
        {
            "dimension": "transcript",
            "kind": "text",
            "value": "hello there",
            "confidence": 1.0,
            "evidence": "",
        },
    ]
    await repo.finish_tag_run(db, org_id, rid, rows, "sha", 1, 1)
    got = [dict(r) for r in await repo.latest_tags(db, org_id, cid)]
    assert [(r["dimension"], r["value"], r["ordinal"]) for r in got] == [
        ("beats", "hook", 0),
        ("beats", "problem", 1),
        ("concept_tags", "pharmacist_warning", None),
        ("messenger", "expert_on_camera", None),
        ("transcript", "hello there", None),
    ]
    assert got[1]["t_start_s"] == 5.0 and got[1]["kind"] == "ordered"
    assert got[2]["raw_value"] == "Pharmacist Warning" and got[2]["kind"] == "open"
    assert got[3]["kind"] == "single" and got[3]["raw_value"] is None
    assert await repo.latest_tag_value(db, org_id, cid, "transcript") == "hello there"
    assert await repo.latest_tag_value(db, org_id, cid, "nope") is None
