from uuid import UUID

from cs.generation import repo
from cs.generation.brand import create_brand_kit
from tests.conftest import dev_headers

H = dev_headers(org="org_gen")


async def test_generation_routes(client, db):
    r = await client.post("/generations", headers=H, json={"kind": "static", "concept": "x"})
    assert r.status_code == 400  # no brand kit
    await client.post(
        "/brand-kit",
        headers=H,
        json={"colors": ["#000"], "fonts": [], "voice_rules": "", "prohibited_claims": []},
    )
    r = await client.post(
        "/generations", headers=H, json={"kind": "static", "concept": "x", "variants": 3}
    )
    assert r.status_code == 200
    gid = r.json()["id"]
    assert await db.fetchval("SELECT job_type FROM job") == "generation.run"
    assert (await client.get("/generations", headers=H)).json()[0]["id"] == gid
    detail = (await client.get(f"/generations/{gid}", headers=H)).json()
    assert detail["status"] == "pending" and detail["assets"] == []


async def test_member_cannot_create_generation(client, db):
    """Regression test: POST /generations spends money on image/video generation and must be
    admin-gated like other consequential routes (create brand kit, activate account)."""
    member = dev_headers(org="org_gen_member", user="u2", role="member")
    await client.post(
        "/brand-kit",
        headers=dev_headers(org="org_gen_member"),
        json={"colors": [], "fonts": [], "voice_rules": "", "prohibited_claims": []},
    )
    r = await client.post("/generations", headers=member, json={"kind": "static", "concept": "x"})
    assert r.status_code == 403


async def test_member_cannot_publish_generated_asset(client, db):
    """Regression test: POST /generated-assets/{id}/publish creates a real ad on Meta and must
    be admin-gated, at least as consequential as account activation."""
    admin = dev_headers(org="org_gen_publish")
    member = dev_headers(org="org_gen_publish", user="u2", role="member")
    org_id = UUID((await client.get("/me", headers=admin)).json()["org_id"])
    kit = await create_brand_kit(db, org_id, [], [], "", [])
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u", concept="c")
    aid = await repo.insert_generated_asset(
        db, org_id, gid, "image", "1:1", 1, "p.jpg", "s", "H", "P", "SHOP_NOW"
    )
    form = {
        "ad_account_id": str(UUID(int=1)),
        "campaign_id": "c",
        "adset_id": "a",
        "page_id": "p",
        "link_url": "https://x",
        "status": "PAUSED",
    }
    r = await client.post(f"/generated-assets/{aid}/publish", headers=member, json=form)
    assert r.status_code == 403
