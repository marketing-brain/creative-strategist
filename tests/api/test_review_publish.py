from cs.generation import repo
from cs.generation.brand import create_brand_kit
from tests.conftest import dev_headers

H = dev_headers(org="org_rev")


async def _asset(client, db):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    return org_id, await repo.insert_generated_asset(
        db, org_id, gid, "image", "1:1", 1, "p.jpg", "s", "H", "P", "SHOP_NOW"
    )


async def test_review_transitions(client, db):
    _, aid = await _asset(client, db)
    draft = (await client.get("/review-queue?state=draft", headers=H)).json()
    assert draft["total"] == 1 and len(draft["items"]) == 1
    assert (await client.post(f"/generated-assets/{aid}/approve", headers=H)).json() == {"ok": True}
    approved = (await client.get("/review-queue?state=approved", headers=H)).json()
    assert approved["total"] == 1 and len(approved["items"]) == 1
    assert (await client.post(f"/generated-assets/{aid}/reject", headers=H)).json() == {"ok": False}


async def test_review_queue_pagination(client, db):
    org_id, _ = await _asset(client, db)
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    for i in range(2, 4):
        await repo.insert_generated_asset(
            db, org_id, gid, "image", "1:1", i, f"p{i}.jpg", "s", "H", "P", "SHOP_NOW"
        )

    page1 = (await client.get("/review-queue?state=draft&limit=2&offset=0", headers=H)).json()
    assert page1["total"] == 3 and len(page1["items"]) == 2

    page2 = (await client.get("/review-queue?state=draft&limit=2&offset=2", headers=H)).json()
    assert page2["total"] == 3 and len(page2["items"]) == 1

    page1_ids = {i["id"] for i in page1["items"]}
    page2_ids = {i["id"] for i in page2["items"]}
    assert not page1_ids & page2_ids
