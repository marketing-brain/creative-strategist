from tests.conftest import dev_headers

H = dev_headers(org="org_brand")


async def test_brand_kit_routes(client, blobs):
    assert (await client.get("/brand-kit", headers=H)).status_code == 404
    r = await client.post("/brand-kit", headers=H, json={"colors": ["#000"], "fonts": ["Inter"], "voice_rules": "v", "prohibited_claims": ["x"]})
    assert r.status_code == 200 and r.json()["version"] == 1
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = await client.post("/brand-kit/logo", headers=H, files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["logo_url"].endswith("/logo.png")
    assert (await client.get("/brand-kit", headers=H)).json()["colors"] == ["#000"]
