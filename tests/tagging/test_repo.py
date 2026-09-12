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


async def test_untagged_creative_ids_excludes_unresolved_by_default(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD")
    img_id, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("img", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="meta_object_type",
    )
    unresolved_id, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("unresolved", "unresolved", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="unresolved",
    )
    # Give both a primary asset so the exclusion below is provably about `format`,
    # not the separate `primary_asset_id IS NOT NULL` guard in the same query.
    for cid in (img_id, unresolved_id):
        asset_id = await ingest_repo.insert_asset(
            db, org_id, cid, "original_image", f"p/{cid}.png", "sha", "image/png"
        )
        await ingest_repo.set_primary_asset(db, org_id, cid, asset_id)

    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    ids = await repo.untagged_creative_ids(db, org_id, tv, "fake-model", 50)
    assert ids == [img_id]
