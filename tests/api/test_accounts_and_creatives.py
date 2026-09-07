from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.ingest import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_api")


async def _seed(db, settings, client):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(
        db, org_id, "1", "Main", "USD",
    )
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "raw",
    )
    aid = await repo.insert_asset(
        db, org_id, cid, "original_image", "o/c1/original.png", "sha", "image/png"
    )
    await repo.set_primary_asset(db, org_id, cid, aid)
    ad = await repo.upsert_ad(
        db, org_id, acct, PlatformAd("ad1", "A", "ACTIVE", None, None, "c1"), cid
    )
    await repo.upsert_metrics(
        db,
        org_id,
        acct,
        [
            MetricRow(
                "ad1",
                date(2026, 9, 1),
                "facebook:feed",
                Decimal("10"),
                1000,
                20,
                20,
                Decimal("2"),
                Decimal("100"),
                0,
                0,
                0,
                0,
                {},
            )
        ],
    )
    return org_id, acct, cid, ad


async def test_accounts_activate_enqueues_sync(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    from cs.ingest import repo as ingest_repo

    conn_id = await ingest_repo.upsert_meta_connection(db, org_id, "conn_1", "s")
    await db.execute("UPDATE ad_account SET meta_connection_id=$2 WHERE id=$1", acct, conn_id)
    r = await client.get("/accounts", headers=H)
    assert r.json()[0]["status"] == "paused"
    r = await client.post(f"/accounts/{acct}/activate", headers=H)
    assert r.status_code == 200 and r.json()["job_id"]
    assert await db.fetchval("SELECT job_type FROM job") == "meta.sync_from_fivetran"
    assert (await client.get("/accounts", headers=H)).json()[0]["status"] == "active"


async def test_member_cannot_activate(client, db, settings):
    _, acct, _, _ = await _seed(db, settings, client)
    r = await client.post(
        f"/accounts/{acct}/activate", headers=dev_headers(org="org_api", user="u2", role="member")
    )
    assert r.status_code == 403


async def test_creatives_list_detail_metrics(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get("/creatives", headers=H)
    assert r.status_code == 200 and r.json()[0]["primary_asset_url"].endswith(
        "/blobs/o/c1/original.png"
    )
    r = await client.get(f"/creatives/{cid}", headers=H)
    assert r.json()["headline"] == "H"
    r = await client.get(f"/creatives/{cid}/metrics?since=2026-08-01&until=2026-09-30", headers=H)
    row = r.json()[0]
    assert row["ctr"] == 0.02 and row["cpa"] == 5.0 and row["roas"] == 10.0


async def test_other_org_cannot_see_creatives(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get(f"/creatives/{cid}", headers=dev_headers(org="org_other"))
    assert r.status_code == 404


async def test_creatives_limit_is_validated(client, db, settings):
    await _seed(db, settings, client)
    assert (await client.get("/creatives?limit=-1", headers=H)).status_code == 422
    assert (await client.get("/creatives?limit=201", headers=H)).status_code == 422
    assert (await client.get("/creatives?offset=-1", headers=H)).status_code == 422
    assert (await client.get("/creatives?limit=200", headers=H)).status_code == 200


async def test_creative_metrics_counts_are_integers(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get(f"/creatives/{cid}/metrics?since=2026-08-01&until=2026-09-30", headers=H)
    row = r.json()[0]
    for field in ("impressions", "clicks", "link_clicks", "video_3s_views", "video_thruplay"):
        assert isinstance(row[field], int), field
    assert row["impressions"] == 1000 and row["clicks"] == 20
    assert row["spend"] == 10.0
