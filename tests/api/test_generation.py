from tests.conftest import dev_headers

H = dev_headers(org="org_gen")


async def test_generation_routes(client, db):
    r = await client.post("/generations", headers=H, json={"kind": "static", "concept": "x"})
    assert r.status_code == 400  # no brand kit
    await client.post("/brand-kit", headers=H, json={"colors": ["#000"], "fonts": [], "voice_rules": "", "prohibited_claims": []})
    r = await client.post("/generations", headers=H, json={"kind": "static", "concept": "x", "variants": 3})
    assert r.status_code == 200
    gid = r.json()["id"]
    assert await db.fetchval("SELECT job_type FROM job") == "generation.run"
    assert (await client.get("/generations", headers=H)).json()[0]["id"] == gid
    detail = (await client.get(f"/generations/{gid}", headers=H)).json()
    assert detail["status"] == "pending" and detail["assets"] == []
