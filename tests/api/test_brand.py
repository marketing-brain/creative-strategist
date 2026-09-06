from uuid import UUID

from tests.conftest import dev_headers

H = dev_headers(org="org_brand")


async def test_brand_kit_routes(client, blobs):
    assert (await client.get("/brand-kit", headers=H)).status_code == 404
    r = await client.post(
        "/brand-kit",
        headers=H,
        json={
            "colors": ["#000"],
            "fonts": ["Inter"],
            "voice_rules": "v",
            "prohibited_claims": ["x"],
        },
    )
    assert r.status_code == 200 and r.json()["version"] == 1
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    r = await client.post(
        "/brand-kit/logo", headers=H, files={"file": ("logo.png", png, "image/png")}
    )
    assert (
        r.status_code == 200
        and r.json()["version"] == 2
        and r.json()["logo_url"].endswith("/logo.png")
    )
    assert (await client.get("/brand-kit", headers=H)).json()["colors"] == ["#000"]


async def test_brand_kit_logo_rejects_svg(client, blobs):
    """Regression test: compose.overlay_logo opens logo bytes with PIL.Image.open, which cannot
    decode SVG -- accepting SVG at upload time crashed every later generation for that org."""
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    r = await client.post(
        "/brand-kit/logo", headers=H, files={"file": ("logo.svg", svg, "image/svg+xml")}
    )
    assert r.status_code == 400


async def test_brand_kit_logo_versions_not_corrupted(client, blobs, db):
    """Regression test: uploading different logos should not overwrite previous versions' blobs."""
    from cs.generation.brand import get_brand_kit

    # Get the org_id from the client's auth context
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])

    # Create initial brand kit with first logo
    png1 = b"\x89PNG\r\n\x1a\n" + b"1" * 32
    r1 = await client.post(
        "/brand-kit/logo", headers=H, files={"file": ("logo.png", png1, "image/png")}
    )
    assert r1.status_code == 200
    v1 = r1.json()["version"]
    url1 = r1.json()["logo_url"]
    kit_id_1 = r1.json()["id"]

    # Upload different logo, creating version 2
    png2 = b"\x89PNG\r\n\x1a\n" + b"2" * 32
    r2 = await client.post(
        "/brand-kit/logo", headers=H, files={"file": ("logo.png", png2, "image/png")}
    )
    assert r2.status_code == 200
    v2 = r2.json()["version"]
    url2 = r2.json()["logo_url"]
    kit_id_2 = r2.json()["id"]

    # Verify the blob keys are different (content-addressed)
    assert url1 != url2, "Different logo content should produce different URLs"

    # Verify both URLs end with /logo.png (satisfies test contract)
    assert url1.endswith("/logo.png")
    assert url2.endswith("/logo.png")

    # Verify version 1 kit still has its original logo
    kit_v1 = await get_brand_kit(db, org_id, UUID(kit_id_1))
    assert kit_v1 is not None
    assert kit_v1.version == v1

    # Verify version 2 kit has different logo
    kit_v2 = await get_brand_kit(db, org_id, UUID(kit_id_2))
    assert kit_v2 is not None
    assert kit_v2.version == v2
    assert kit_v1.logo_path != kit_v2.logo_path, (
        "Different versions should have different logo paths"
    )

    # Verify we can fetch both logos and they have distinct content
    blob1 = await blobs.get(kit_v1.logo_path)
    blob2 = await blobs.get(kit_v2.logo_path)
    assert blob1 == png1, "Version 1 logo should still have original content"
    assert blob2 == png2, "Version 2 logo should have new content"
    assert blob1 != blob2, "The two logo versions should have different content"
