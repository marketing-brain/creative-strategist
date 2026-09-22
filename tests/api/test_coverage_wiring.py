"""The two endpoints that carry "what we could not see" to the pages.

Both numbers have been wrong on the page while the functions behind them were right, so
these go through the API: the scope line must describe the stored run's own files, and a
creative with no tags must arrive with the gate's reason attached.
"""

from datetime import date
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.core.ids import new_id
from cs.core.repo.assets import insert_asset, set_primary_asset
from cs.insights.account_read import repo
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy
from tests.conftest import dev_headers

H = dev_headers(org="org_cov")


async def _account(client, db):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    aid = new_id()
    await db.execute(
        "INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, status) "
        "VALUES ($1,$2,'meta','act_cov','A','active')",
        aid,
        org_id,
    )
    return org_id, aid


async def _video(
    db, org_id, aid, name, sha, spend, *, tagged: bool, impressions=10_000, plays=8_000
):
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        aid,
        PlatformCreative(name, "video", f"H {name}", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    asset = await insert_asset(
        db, org_id, cid, "original_video", f"v/{sha}.mp4", sha, "video/mp4", duration_ms=30_000
    )
    await set_primary_asset(db, org_id, cid, asset)
    await ingest_repo.upsert_ad(
        db, org_id, aid, PlatformAd(f"ad_{name}", name, "ACTIVE", "as", "cp", name), cid
    )
    await ingest_repo.upsert_metrics(
        db,
        org_id,
        aid,
        [
            MetricRow(
                f"ad_{name}",
                date(2026, 6, 1),
                "facebook:feed",
                Decimal(spend),
                impressions,
                300,
                300,
                Decimal(10),
                Decimal(500),
                4_000,
                1_200,
                3_000,
                1_000,
                {},
                0,
                plays,
                5_000,
                2_000,
                1_100,
                Decimal(0),
                purchases=10,
            )
        ],
    )
    if tagged:
        tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
        run_id = await tag_repo.start_tag_run(db, org_id, cid, tv, "fake")
        assert run_id is not None
        await tag_repo.finish_tag_run(
            db,
            org_id,
            run_id,
            [
                {
                    "dimension": "messenger",
                    "value": "founder",
                    "kind": "single",
                    "confidence": 1.0,
                    "evidence": "a founder to camera",
                }
            ],
            "sha",
            10,
            5,
        )
    return cid


async def test_account_read_reports_the_run_s_own_coverage(client, db):
    org_id, aid = await _account(client, db)
    await _video(db, org_id, aid, "seen", "sha_seen", 900, tagged=True)
    await _video(db, org_id, aid, "unseen", "sha_unseen", 100, tagged=False)
    await repo.insert_run(
        db,
        org_id,
        aid,
        status="done",
        n_units=1,
        result={
            "summary": {"units": 1, "spend": 900},
            "tests": [],
            "units": [{"sha256": "sha_seen", "headline": "seen", "spend": 900}],
        },
        read={"overview": "o", "findings": []},
        error=None,
    )

    body = (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)).json()
    assert body["coverage"] == {"files": 2, "tagged_files": 1, "spend_share": 0.9}


async def test_a_creative_below_the_floors_carries_the_reason(client, db):
    org_id, aid = await _account(client, db)
    # Under both floors: too few impressions for a rate, too few plays for the curve.
    cid = await _video(
        db, org_id, aid, "tiny", "sha_tiny", 5, tagged=False, impressions=40, plays=20
    )

    status = (await client.get(f"/creatives/{cid}", headers=H)).json()["tag_status"]
    assert status["reason"] == "below_threshold"
    assert "40 impressions" in status["detail"]
    assert "2,000 impressions" in status["detail"]


async def test_a_creative_that_will_be_tagged_names_no_blocker(client, db):
    org_id, aid = await _account(client, db)
    cid = await _video(db, org_id, aid, "big", "sha_big", 900, tagged=False, impressions=50_000)

    status = (await client.get(f"/creatives/{cid}", headers=H)).json()["tag_status"]
    assert status["reason"] is None and status["detail"] is None
