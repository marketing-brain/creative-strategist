"""One tag run stands for a file.

Meta mints a new creative for every copied ad, so one video file is often tagged several
times from scratch, and those runs disagree. The creative page used to show the viewed copy's
own run while the account read judged its patterns on the highest-spend copy's run. On
2026-09-20 that put "try it with opening shot: face to camera" above tags that said face to
camera. Both sides now read the same run."""

from datetime import date
from uuid import UUID

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.tagging import repo
from cs.tagging.taxonomy import load_taxonomy

SHA = "f" * 64


async def _copy(db, org_id, acct, pid: str, *, spend: int, opening: str, sha: str = SHA) -> UUID:
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(pid, "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    aid = await ingest_repo.insert_asset(
        db, org_id, cid, "original_video", f"k/{pid}", sha, "video/mp4"
    )
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    await ingest_repo.upsert_ad(
        db, org_id, acct, PlatformAd("ad_" + pid, pid, "ACTIVE", None, None, pid), cid
    )
    ad_id = await db.fetchval(
        "SELECT id FROM ad WHERE org_id=$1 AND platform_ad_id=$2", org_id, "ad_" + pid
    )
    await db.execute(
        "INSERT INTO metric_daily (org_id, ad_account_id, creative_id, ad_id, day, placement, "
        "spend, impressions) VALUES ($1,$2,$3,$4,$5,'all',$6,1000)",
        org_id,
        acct,
        cid,
        ad_id,
        date(2026, 6, 1),
        spend,
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
    await repo.finish_tag_run(
        db,
        org_id,
        rid,
        [{"dimension": "opening_shot", "value": opening, "confidence": 1.0, "evidence": "e"}],
        "s",
        1,
        1,
    )
    return cid


async def test_every_copy_of_a_file_shows_the_highest_spend_copys_tags(db, org_id, ad_account_id):
    big = await _copy(db, org_id, ad_account_id, "big", spend=900, opening="product_in_hand")
    small = await _copy(db, org_id, ad_account_id, "small", spend=10, opening="face_to_camera")
    for cid in (big, small):
        ids = await ingest_repo.footage_member_ids(db, org_id, cid)
        tags = await repo.footage_latest_tags(db, org_id, ids)
        assert [t["value"] for t in tags] == ["product_in_hand"]


async def test_a_copy_that_was_never_tagged_shows_the_files_tags(db, org_id, ad_account_id):
    await _copy(db, org_id, ad_account_id, "tagged", spend=5, opening="face_to_camera")
    untagged, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        ad_account_id,
        PlatformCreative("untagged", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    aid = await ingest_repo.insert_asset(
        db, org_id, untagged, "original_video", "k/untagged", SHA, "video/mp4"
    )
    await ingest_repo.set_primary_asset(db, org_id, untagged, aid)
    ids = await ingest_repo.footage_member_ids(db, org_id, untagged)
    tags = await repo.footage_latest_tags(db, org_id, ids)
    assert [t["value"] for t in tags] == ["face_to_camera"]


async def test_a_different_file_keeps_its_own_tags(db, org_id, ad_account_id):
    await _copy(db, org_id, ad_account_id, "a", spend=900, opening="product_in_hand")
    other = await _copy(
        db, org_id, ad_account_id, "b", spend=1, opening="scene_broll", sha="e" * 64
    )
    ids = await ingest_repo.footage_member_ids(db, org_id, other)
    tags = await repo.footage_latest_tags(db, org_id, ids)
    assert [t["value"] for t in tags] == ["scene_broll"]
