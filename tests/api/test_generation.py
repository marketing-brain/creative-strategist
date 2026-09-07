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
