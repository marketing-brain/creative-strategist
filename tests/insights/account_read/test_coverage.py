"""The scope line on /insights: how much of an account a read actually saw.

It has been wrong twice, each time by comparing numbers from different moments: once the
count of files from a stored run beside the spend share of everything tagged today (99%),
once a run's spend as recorded then over the account's total now (87%). The truth for that
run was 95%. These tests pin the rule that fixes both: one file set, one moment.
"""

from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.core.repo.assets import insert_asset, set_primary_asset
from cs.footage.refresh import refresh
from cs.insights.account_read.repo import coverage
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy


async def _file(db, org_id, aid, name, sha, spend, *, tagged: bool) -> str:
    """One video file with one ad, its spend, and optionally a finished tag run."""
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
                10_000,
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
                8_000,
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
    return sha


async def test_share_is_the_named_files_over_the_account(db, org_id, ad_account_id):
    a = await _file(db, org_id, ad_account_id, "a", "sha_a", 900, tagged=True)
    await _file(db, org_id, ad_account_id, "b", "sha_b", 100, tagged=False)
    await refresh(db, org_id)

    read = await coverage(db, org_id, ad_account_id, shas=[a])
    assert read == {"files": 2, "tagged_files": 1, "spend_share": 0.9}


async def test_a_read_that_named_no_files_claims_no_share(db, org_id, ad_account_id):
    """The bug that produced "99%": with no file set, the share described everything tagged
    today, which has nothing to do with what a stored read saw."""
    await _file(db, org_id, ad_account_id, "a", "sha_a", 900, tagged=True)
    await _file(db, org_id, ad_account_id, "b", "sha_b", 100, tagged=False)
    await refresh(db, org_id)

    assert (await coverage(db, org_id, ad_account_id, shas=[]))["spend_share"] is None


async def test_tagged_count_does_not_wait_for_the_nightly_refresh(db, org_id, ad_account_id):
    """The grid's "N files are untagged" is about what a filter can match right now, so it
    has to follow the tag runs, not the derived attributes a nightly job writes hours later."""
    await _file(db, org_id, ad_account_id, "a", "sha_a", 900, tagged=True)
    await refresh(db, org_id)  # attributes exist for a
    await _file(db, org_id, ad_account_id, "b", "sha_b", 100, tagged=True)  # tagged since

    c = await coverage(db, org_id, ad_account_id)
    assert c["files"] == 2
    assert c["tagged_files"] == 2, "a file tagged after the last refresh still counts"


async def test_an_account_with_no_spend_has_no_share(db, org_id, ad_account_id):
    await _file(db, org_id, ad_account_id, "a", "sha_a", 0, tagged=True)
    await refresh(db, org_id)

    assert (await coverage(db, org_id, ad_account_id, shas=["sha_a"]))["spend_share"] is None


async def test_unknown_account_has_no_coverage(db, org_id, ad_account_id):
    assert await coverage(db, org_id, ad_account_id) is None
