from uuid import uuid4

from cs.connectors.base import PlatformCreative
from cs.ingest import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy
from tests.conftest import dev_headers

H = dev_headers(org="org_tags")


async def _seed(client, db, settings):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(
        db,
        org_id,
        rid,
        [{"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e"}],
        "s",
        1,
        1,
    )
    return cid


async def test_tags_endpoints(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/tags", headers=H)
    assert r.json() == [{"dimension": "tone", "value": "warm", "confidence": 0.8, "evidence": "e"}]
    r = await client.get("/tags/dimensions", headers=H)
    assert any(d["name"] == "hook_type" for d in r.json())
    r = await client.post(f"/creatives/{cid}/retag", headers=H)
    assert r.status_code == 200
    assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"
    r = await client.post("/tagging/backfill", headers=H, json={"limit": 10})
    assert (
        r.status_code == 200
        and await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.batch_submit'") == 1
    )
    r = await client.get(f"/creatives/{cid}/similar", headers=H)
    assert r.status_code == 200 and r.json() == []


async def test_similar_404_on_nonexistent_creative(client, db, settings):
    await _seed(client, db, settings)
    r = await client.get(f"/creatives/{uuid4()}/similar", headers=H)
    assert r.status_code == 404


async def test_similar_rejects_out_of_range_k(client, db, settings):
    cid = await _seed(client, db, settings)
    r = await client.get(f"/creatives/{cid}/similar?k=-1", headers=H)
    assert r.status_code == 422
    r = await client.get(f"/creatives/{cid}/similar?k=0", headers=H)
    assert r.status_code == 422


async def test_backfill_rejects_out_of_range_limit(client, db, settings):
    await _seed(client, db, settings)
    r = await client.post("/tagging/backfill", headers=H, json={"limit": -1})
    assert r.status_code == 422
    r = await client.post("/tagging/backfill", headers=H, json={"limit": 0})
    assert r.status_code == 422
