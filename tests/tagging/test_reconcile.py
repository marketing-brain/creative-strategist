import io

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.core.ids import new_id
from cs.ingest import repo as ingest_repo
from cs.tagging.reconcile import reenqueue_untagged


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (16, 16)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def untagged_and_tagged(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    ids = []
    for n in ("c1", "c2"):
        cid, _ = await ingest_repo.upsert_creative(
            db, org_id, acct, PlatformCreative(n, "image", "H", "P", None, None, "u", None, None, {}), "raw")
        key = f"{org_id}/creatives/{cid}/original.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(db, org_id, cid, "original_image", key, "sha", "image/png")
        await ingest_repo.set_primary_asset(db, org_id, cid, aid)
        ids.append(cid)
    tv_id = new_id()
    await db.execute("INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, '1.0', 'sha-x')", tv_id)
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status) "
        "VALUES ($1,$2,$3,$4,'fake','done')", new_id(), org_id, ids[1], tv_id)
    return ids  # (untagged, tagged)


async def test_reenqueues_only_creatives_without_a_done_run(db, untagged_and_tagged):
    untagged, _tagged = untagged_and_tagged
    n = await reenqueue_untagged(db)
    assert n == 1
    job = await db.fetchrow("SELECT job_type, payload FROM job")
    assert job["job_type"] == "tagging.tag_creative" and job["payload"]["creative_id"] == str(untagged)


async def test_reenqueue_is_a_noop_against_a_pending_job(db, untagged_and_tagged):
    await reenqueue_untagged(db)
    n = await reenqueue_untagged(db)
    assert n == 0
    assert await db.fetchval("SELECT count(*) FROM job") == 1


async def test_reenqueue_revives_a_dead_lettered_job(db, org_id, untagged_and_tagged):
    untagged, _tagged = untagged_and_tagged
    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(untagged)},
                        dedup_key=f"tag:{untagged}")
    await db.execute("UPDATE job SET status='dead' WHERE job_type='tagging.tag_creative'")
    n = await reenqueue_untagged(db)
    assert n == 1
    assert await db.fetchval("SELECT count(*) FROM job WHERE status='pending'") == 1
