from datetime import date
from uuid import UUID

from cs.connectors.base import PlatformCreative
from cs.core import repo
from cs.insights import repo as insights_repo
from tests.conftest import dev_headers
from tests.insights.test_creative_insight_runner import _account, _png
from tests.insights.test_creative_insight_runner import _creative as _delivering

H = dev_headers(org="org_ci")
H_OTHER = dev_headers(org="org_ci_other")


async def _creative(db, client) -> tuple[UUID, UUID]:
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("x", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    return org_id, cid


async def _insert(db, org_id, cid, status: str) -> None:
    await insights_repo.insert_creative_insight(
        db, org_id, cid, status=status, paragraph="Attention is strong.", moments=[],
        problems=[], brief="b", window_start=date(2026, 8, 15), window_end=date(2026, 9, 13),
        model="fake", prompt_sha256="s", input_tokens=1, output_tokens=1,
    )  # fmt: skip


async def test_insight_endpoint_serves_a_done_paragraph(client, db):
    org_id, cid = await _creative(db, client)
    r = await client.get(f"/creatives/{cid}/insight", headers=H)
    assert r.status_code == 200 and r.json() == {"paragraph": None, "reason": "no_asset"}
    await _insert(db, org_id, cid, "done")
    body = (await client.get(f"/creatives/{cid}/insight", headers=H)).json()
    assert body["paragraph"] == "Attention is strong."
    assert body["window_start"] == "2026-08-15" and body["window_end"] == "2026-09-13"


async def test_rejected_insight_is_never_served(client, db):
    org_id, cid = await _creative(db, client)
    await _insert(db, org_id, cid, "rejected")
    body = (await client.get(f"/creatives/{cid}/insight", headers=H)).json()
    assert body == {"paragraph": None, "reason": "rejected"}


async def test_insight_404s_cross_org(client, db):
    org_id, cid = await _creative(db, client)
    await _insert(db, org_id, cid, "done")
    assert (await client.get(f"/creatives/{cid}/insight", headers=H_OTHER)).status_code == 404


async def _reason(client, cid) -> dict:
    return (await client.get(f"/creatives/{cid}/insight", headers=H)).json()


async def test_empty_insight_names_the_format_limit(client, db, blobs, settings):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await _account(db, org_id)
    cap = settings.creative_insight_limit_per_format
    for i in range(cap + 1):
        cid = await _delivering(
            db, blobs, org_id, acct, f"c{i}", "image", daily_impressions=5_000, ctr=0.02,
            data=_png(), sha=f"sha{i}",  # distinct files: one shared hash is one footage group
        )  # fmt: skip
        if i < cap:
            await _insert(db, org_id, cid, "done")
    assert await _reason(client, cid) == {
        "paragraph": None, "reason": "format_limit", "format": "image", "limit": cap,
    }  # fmt: skip


async def test_empty_insight_names_thin_delivery(client, db, blobs):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await _account(db, org_id)
    cid = await _delivering(
        db, blobs, org_id, acct, "t", "image", daily_impressions=20, ctr=0.02, data=_png()
    )
    await _delivering(db, blobs, org_id, acct, "peer", "image", daily_impressions=5_000, ctr=0.01)
    assert await _reason(client, cid) == {"paragraph": None, "reason": "unreadable"}


async def test_qualifying_creative_is_waiting_or_paused_with_the_nightly_run(client, db, blobs):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    acct = await _account(db, org_id)
    cid = await _delivering(
        db, blobs, org_id, acct, "t", "image", daily_impressions=5_000, ctr=0.02, data=_png()
    )
    await _delivering(db, blobs, org_id, acct, "peer", "image", daily_impressions=5_000, ctr=0.01)
    assert await _reason(client, cid) == {"paragraph": None, "reason": "waiting"}
    await db.execute(
        "INSERT INTO schedule_state (name, last_run_at) VALUES ($1, '2100-01-01') "
        "ON CONFLICT (name) DO UPDATE SET last_run_at = EXCLUDED.last_run_at",
        "insights.nightly_creative_insights",
    )
    assert await _reason(client, cid) == {"paragraph": None, "reason": "paused"}
