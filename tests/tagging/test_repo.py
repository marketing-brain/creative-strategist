from cs.connectors.base import PlatformCreative
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy


async def test_finish_tag_run_is_idempotent_on_retry(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    cid, _ = await ingest_repo.upsert_creative(
        db, org_id, acct, PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}), "raw"
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    await repo.finish_tag_run(db, org_id, rid, tags, "sha", 1, 1)
    await repo.finish_tag_run(db, org_id, rid, tags, "sha", 1, 1)  # simulates a reclaimed/retried job
    rows = await db.fetch("SELECT dimension, value FROM tag WHERE tag_run_id=$1", rid)
    assert len(rows) == 1
