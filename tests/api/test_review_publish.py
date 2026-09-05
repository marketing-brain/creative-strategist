from cs.generation import repo
from cs.generation.brand import create_brand_kit
from tests.conftest import dev_headers

H = dev_headers(org="org_rev")


async def _asset(client, db):
    from uuid import UUID
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    return org_id, await repo.insert_generated_asset(db, org_id, gid, "image", "1:1", 1, "p.jpg", "s", "H", "P", "SHOP_NOW")


async def test_review_transitions_and_publish_gate(client, db, settings):
    from cs.core.crypto import encrypt_str
    from cs.ingest import repo as ingest_repo
    org_id, aid = await _asset(client, db)
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None)
    form = {"ad_account_id": str(acct), "campaign_id": "c", "adset_id": "a", "page_id": "p", "link_url": "https://x", "status": "PAUSED"}
    assert (await client.post(f"/generated-assets/{aid}/publish", headers=H, json=form)).status_code == 409
    assert len((await client.get("/review-queue?state=draft", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/approve", headers=H)).json() == {"ok": True}
    assert (await client.post(f"/generated-assets/{aid}/reject", headers=H)).json() == {"ok": False}
    r = await client.post(f"/generated-assets/{aid}/publish", headers=H, json=form)
    assert r.status_code == 200 and await db.fetchval("SELECT job_type FROM job") == "publish.meta"
    assert (await client.get("/publish-jobs", headers=H)).json()[0]["status"] == "pending"
