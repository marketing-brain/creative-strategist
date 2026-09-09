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
