import httpx
import respx

from cs.connectors.base import PlatformCreative
from cs.core import repo
from cs.ingest import assets
from cs.jobs.registry import JobContext


async def _creative(db, org_id):
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cr1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "",
        format_source="manual_review",
    )
    return cid


@respx.mock
async def test_download_creative_stores_asset_and_queues_tagging(db, blobs, settings, org_id):
    respx.get("https://cdn.example/img.jpg").mock(
        return_value=httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )
    cid = await _creative(db, org_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        await assets.download_creative(
            ctx, http, cid, "https://cdn.example/img.jpg", "original_image"
        )
    assert await repo.creative_has_primary_asset(db, org_id, cid)
    assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"


@respx.mock
async def test_download_creative_stores_the_verdict_once_the_file_lands(
    db, blobs, settings, org_id
):
    respx.get("https://cdn.example/v.mp4").mock(
        return_value=httpx.Response(200, content=b"vid", headers={"content-type": "video/mp4"})
    )
    cid = await _creative(db, org_id)
    await db.execute(
        "UPDATE creative SET format='unresolved', format_source='unresolved' WHERE id=$1", cid
    )
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        await assets.download_creative(
            ctx,
            http,
            cid,
            "https://cdn.example/v.mp4",
            "original_video",
            verdict=("video", "asset_feed_spec_video"),
        )

    row = await db.fetchrow(
        "SELECT format, format_source, format_verified_at FROM creative WHERE id=$1", cid
    )
    assert (row["format"], row["format_source"]) == ("video", "asset_feed_spec_video")
    assert row["format_verified_at"] is not None


@respx.mock
async def test_download_creative_that_fails_leaves_the_creative_unresolved(
    db, blobs, settings, org_id
):
    respx.get("https://cdn.example/gone.mp4").mock(return_value=httpx.Response(403))
    cid = await _creative(db, org_id)
    await db.execute(
        "UPDATE creative SET format='unresolved', format_source='unresolved' WHERE id=$1", cid
    )
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        try:
            await assets.download_creative(
                ctx,
                http,
                cid,
                "https://cdn.example/gone.mp4",
                "original_video",
                verdict=("video", "asset_feed_spec_video"),
            )
        except httpx.HTTPStatusError:
            pass

    assert await db.fetchval("SELECT format FROM creative WHERE id=$1", cid) == "unresolved"


@respx.mock
async def test_download_creative_skips_tagging_when_video_tagging_disabled(
    db, blobs, settings, org_id
):
    settings.video_tagging_enabled = False
    try:
        respx.get("https://cdn.example/v.mp4").mock(
            return_value=httpx.Response(200, content=b"vid", headers={"content-type": "video/mp4"})
        )
        cid = await _creative(db, org_id)
        ctx = JobContext(
            db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})()
        )
        async with httpx.AsyncClient() as http:
            await assets.download_creative(
                ctx, http, cid, "https://cdn.example/v.mp4", "original_video"
            )
        assert await repo.creative_has_primary_asset(db, org_id, cid)
        assert await db.fetchval("SELECT count(*) FROM job") == 0
    finally:
        settings.video_tagging_enabled = True


@respx.mock
async def test_download_creative_still_tags_images_when_video_tagging_disabled(
    db, blobs, settings, org_id
):
    settings.video_tagging_enabled = False
    try:
        respx.get("https://cdn.example/img2.jpg").mock(
            return_value=httpx.Response(
                200, content=b"\x89PNG", headers={"content-type": "image/png"}
            )
        )
        cid = await _creative(db, org_id)
        ctx = JobContext(
            db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})()
        )
        async with httpx.AsyncClient() as http:
            await assets.download_creative(
                ctx, http, cid, "https://cdn.example/img2.jpg", "original_image"
            )
        assert await db.fetchval("SELECT job_type FROM job") == "tagging.tag_creative"
    finally:
        settings.video_tagging_enabled = True


@respx.mock
async def test_download_carousel_stores_each_card_in_order(db, blobs, settings, org_id):
    respx.get("https://cdn.example/0.jpg").mock(
        return_value=httpx.Response(200, content=b"a", headers={"content-type": "image/jpeg"})
    )
    respx.get("https://cdn.example/1.jpg").mock(
        return_value=httpx.Response(200, content=b"b", headers={"content-type": "image/jpeg"})
    )
    cid = await _creative(db, org_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=type("J", (), {"org_id": org_id})())
    async with httpx.AsyncClient() as http:
        await assets.download_carousel(
            ctx, http, cid, ["https://cdn.example/0.jpg", "https://cdn.example/1.jpg"]
        )
    cards = await repo.list_assets_by_kind(db, org_id, cid, "original_image")
    assert [c["seq"] for c in cards] == [0, 1]
    assert await repo.creative_has_primary_asset(db, org_id, cid)
