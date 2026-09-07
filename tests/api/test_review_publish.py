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
    assert len((await client.get("/review-queue?state=draft", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/approve", headers=H)).json() == {"ok": True}
    assert len((await client.get("/review-queue?state=approved", headers=H)).json()) == 1
    assert (await client.post(f"/generated-assets/{aid}/reject", headers=H)).json() == {"ok": False}
