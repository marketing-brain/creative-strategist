from cs.generation.brand import create_brand_kit, get_active_brand_kit


async def test_versions_and_active(db, org_id):
    assert await get_active_brand_kit(db, org_id) is None
    k1 = await create_brand_kit(db, org_id, ["#112233"], ["Inter"], "Warm, direct.", ["cures"])
    k2 = await create_brand_kit(
        db,
        org_id,
        ["#112233", "#FFFFFF"],
        ["Inter"],
        "Warm, direct.",
        ["cures"],
        "o/logo.png",
        "sha",
    )
    assert (k1.version, k2.version) == (1, 2)
    active = await get_active_brand_kit(db, org_id)
    assert (
        active.id == k2.id
        and active.logo_path == "o/logo.png"
        and active.prohibited_claims == ["cures"]
    )
