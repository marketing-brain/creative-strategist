from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo
from cs.tagging import repo as tagging_repo
from cs.tagging.taxonomy import load_taxonomy
from tests.conftest import dev_headers

H = dev_headers(org="org_api")


async def _seed(db, settings, client):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "Main",
        "USD",
    )
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "raw",
        format_source="manual_review",
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
    from cs.core import repo as ingest_repo

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
    r = await client.get("/creatives?delivery=all", headers=H)
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    assert body["items"][0]["primary_asset_url"].endswith("/blobs/o/c1/original.png")
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
    r = await client.get("/creatives?delivery=all&limit=-1", headers=H)
    assert r.status_code == 422
    r = await client.get("/creatives?delivery=all&limit=201", headers=H)
    assert r.status_code == 422
    r = await client.get("/creatives?delivery=all&offset=-1", headers=H)
    assert r.status_code == 422
    r = await client.get("/creatives?delivery=all&limit=200", headers=H)
    assert r.status_code == 200


async def test_creatives_pagination(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    for i in range(2, 4):
        await repo.upsert_creative(
            db,
            org_id,
            acct,
            PlatformCreative(
                f"c{i}", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}
            ),
            "raw",
            format_source="manual_review",
        )
    full = (await client.get("/creatives?delivery=all&limit=200", headers=H)).json()
    assert full["total"] == 3 and len(full["items"]) == 3

    page1 = (await client.get("/creatives?delivery=all&limit=2&offset=0", headers=H)).json()
    assert page1["total"] == 3 and len(page1["items"]) == 2

    page2 = (await client.get("/creatives?delivery=all&limit=2&offset=2", headers=H)).json()
    assert page2["total"] == 3 and len(page2["items"]) == 1

    page1_ids = {i["id"] for i in page1["items"]}
    page2_ids = {i["id"] for i in page2["items"]}
    assert not page1_ids & page2_ids


async def test_creatives_filter_by_tag(client, db, settings):
    org_id, acct, cid, _ = await _seed(db, settings, client)
    other_cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c2", "image", "H2", "P2", None, "SHOP_NOW", "https://x", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tv = await tagging_repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await tagging_repo.start_tag_run(db, org_id, cid, tv, "fake")
    await tagging_repo.finish_tag_run(
        db,
        org_id,
        rid,
        [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}],
        "s",
        1,
        1,
    )
    other_rid = await tagging_repo.start_tag_run(db, org_id, other_cid, tv, "fake")
    await tagging_repo.finish_tag_run(
        db,
        org_id,
        other_rid,
        [{"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e"}],
        "s",
        1,
        1,
    )

    r = await client.get("/creatives?delivery=all&dimension=hook_type&value=question", headers=H)
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    assert body["items"][0]["id"] == str(cid)


async def test_creatives_tag_filter_requires_both_params(client, db, settings):
    await _seed(db, settings, client)
    assert (
        await client.get("/creatives?delivery=all&dimension=hook_type", headers=H)
    ).status_code == 422
    assert (
        await client.get("/creatives?delivery=all&value=question", headers=H)
    ).status_code == 422


async def test_creatives_spend_30d_excludes_older_metrics(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    recent_cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(
            "c_recent", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}
        ),
        "raw",
        format_source="manual_review",
    )
    await repo.upsert_ad(
        db, org_id, acct, PlatformAd("ad_recent", "A", "ACTIVE", None, None, "c_recent"), recent_cid
    )
    today = date.today()
    await repo.upsert_metrics(
        db,
        org_id,
        acct,
        [
            MetricRow(
                "ad_recent",
                today - timedelta(days=5),
                "facebook:feed",
                Decimal("50"),
                500,
                10,
                10,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            ),
            MetricRow(
                "ad_recent",
                today - timedelta(days=40),
                "facebook:feed",
                Decimal("999"),
                999,
                999,
                999,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            ),
        ],
    )

    body = (await client.get("/creatives?delivery=all&limit=200", headers=H)).json()
    recent = next(i for i in body["items"] if i["id"] == str(recent_cid))
    assert recent["spend_30d"] == 50.0
    assert recent["totals"]["link_ctr"] == 0.02


async def test_creatives_spend_30d_defaults_to_zero_with_no_metrics(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    zero_cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(
            "c_zero", "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}
        ),
        "raw",
        format_source="manual_review",
    )

    body = (await client.get("/creatives?delivery=all&limit=200", headers=H)).json()
    zero = next(i for i in body["items"] if i["id"] == str(zero_cid))
    assert zero["spend_30d"] == 0.0
    assert zero["totals"]["link_ctr"] is None


async def test_creative_metrics_counts_are_integers(client, db, settings):
    _, _, cid, _ = await _seed(db, settings, client)
    r = await client.get(f"/creatives/{cid}/metrics?since=2026-08-01&until=2026-09-30", headers=H)
    row = r.json()[0]
    for field in ("impressions", "clicks", "link_clicks", "video_3s_views", "video_thruplay"):
        assert isinstance(row[field], int), field
    assert row["impressions"] == 1000 and row["clicks"] == 20
    assert row["spend"] == 10.0


async def _seed_spender(db, org_id, acct, slug, spend, impressions, clicks):
    """A creative with one recent metric row, or none at all when spend is None."""
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(slug, "image", "H", "P", None, "SHOP_NOW", "https://x", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    if spend is None:
        return cid
    await repo.upsert_ad(
        db, org_id, acct, PlatformAd(f"ad_{slug}", "A", "ACTIVE", None, None, slug), cid
    )
    await repo.upsert_metrics(
        db,
        org_id,
        acct,
        [
            MetricRow(
                f"ad_{slug}",
                date.today() - timedelta(days=5),
                "facebook:feed",
                Decimal(spend),
                impressions,
                clicks,
                clicks,
                Decimal("0"),
                Decimal("0"),
                0,
                0,
                0,
                0,
                {},
            )
        ],
    )
    return cid


async def _sorted_ids(client, query, limit=200):
    body = (await client.get(f"/creatives?delivery=all&limit={limit}&{query}", headers=H)).json()
    return [i["id"] for i in body["items"]]


async def test_creatives_sort_by_spend(client, db, settings):
    org_id, acct, seeded, _ = await _seed(db, settings, client)
    big = await _seed_spender(db, org_id, acct, "c_big", "500", 5000, 50)
    small = await _seed_spender(db, org_id, acct, "c_small", "5", 100, 1)

    desc = await _sorted_ids(client, "sort=spend&order=desc")
    assert desc[0] == str(big)
    assert desc.index(str(big)) < desc.index(str(small))

    asc = await _sorted_ids(client, "sort=spend&order=asc")
    assert asc.index(str(small)) < asc.index(str(big))
    assert asc[-1] == str(big)


async def test_creatives_sort_by_impressions(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    many = await _seed_spender(db, org_id, acct, "c_many", "1", 9000, 10)
    few = await _seed_spender(db, org_id, acct, "c_few", "900", 10, 1)

    desc = await _sorted_ids(client, "sort=impressions&order=desc")
    assert desc[0] == str(many)
    assert desc.index(str(many)) < desc.index(str(few))


async def test_creatives_sort_by_ctr_keeps_undelivered_last_both_ways(client, db, settings):
    org_id, acct, _, _ = await _seed(db, settings, client)
    high = await _seed_spender(db, org_id, acct, "c_high", "10", 1000, 100)  # 10% CTR
    low = await _seed_spender(db, org_id, acct, "c_low", "10", 1000, 5)  # 0.5% CTR
    none = await _seed_spender(db, org_id, acct, "c_none", None, 0, 0)  # no metrics -> NULL CTR

    desc = await _sorted_ids(client, "sort=ctr&order=desc")
    assert desc[0] == str(high)
    assert desc.index(str(high)) < desc.index(str(low)) < desc.index(str(none))

    # Ascending means worst real CTR first, not "never delivered" first.
    asc = await _sorted_ids(client, "sort=ctr&order=asc")
    assert asc.index(str(low)) < asc.index(str(high)) < asc.index(str(none))
    assert asc[-1] == str(none)


async def test_creatives_sort_is_validated(client, db, settings):
    await _seed(db, settings, client)
    assert (
        await client.get("/creatives?delivery=all&sort=spend; DROP TABLE", headers=H)
    ).status_code == 422
    r = await client.get("/creatives?delivery=all&sort=clicks", headers=H)
    assert r.status_code == 422
    assert (
        await client.get("/creatives?delivery=all&order=sideways", headers=H)
    ).status_code == 422
    assert (
        await client.get("/creatives?delivery=all&sort=ctr&order=asc", headers=H)
    ).status_code == 200


async def test_creatives_paging_is_stable_across_tied_rows(client, db, settings):
    """Every creative ties at spend 0, so only the id tiebreak keeps paging from repeating rows."""
    org_id, acct, _, _ = await _seed(db, settings, client)
    for i in range(6):
        await _seed_spender(db, org_id, acct, f"c_tied{i}", None, 0, 0)

    seen = []
    for offset in (0, 3, 6):
        page = await _sorted_ids(client, f"sort=spend&order=desc&offset={offset}", limit=3)
        seen.extend(page)
    # _seed adds one creative with spend, plus the six tied ones.
    assert len(seen) == len(set(seen)) == 7
