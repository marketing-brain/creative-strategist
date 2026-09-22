from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo as ingest_repo
from cs.core.repo.assets import insert_asset, set_primary_asset
from cs.insights.account_read.units import build_units
from cs.tagging import repo as tag_repo
from cs.tagging.taxonomy import load_taxonomy


async def _creative(db, org_id, aid, name, sha, spend, tags):
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        aid,
        PlatformCreative(name, "video", f"H {name}", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    asset = await insert_asset(
        db, org_id, cid, "original_video", f"v/{sha}.mp4", sha, "video/mp4", duration_ms=60_000
    )
    await set_primary_asset(db, org_id, cid, asset)
    await ingest_repo.upsert_ad(
        db, org_id, aid, PlatformAd(f"ad_{name}", name, "ACTIVE", "as", "cp", name), cid
    )
    rows = [
        MetricRow(
            f"ad_{name}",
            date(2026, 6, d),
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
        for d in (1, 2)
    ]
    await ingest_repo.upsert_metrics(db, org_id, aid, rows)
    if tags is not None:
        tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
        rid = await tag_repo.start_tag_run(db, org_id, cid, tv, "fake")
        await tag_repo.finish_tag_run(db, org_id, rid, tags, "s", 1, 1)
    return cid


def _tag(d, k, v, raw=None, t=None, o=None):
    return {
        "dimension": d,
        "value": v,
        "confidence": 1.0,
        "evidence": "",
        "kind": k,
        "raw_value": raw,
        "t_start_s": t,
        "ordinal": o,
    }


async def test_units_pool_copies_by_sha_and_take_facets_from_top_spend_copy(
    db, org_id, ad_account_id
):
    aid = ad_account_id
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", aid)
    v2 = [
        _tag("messenger", "single", "creator_on_camera"),
        _tag("duration_s", "numeric", "60"),
        _tag("concept_summary", "text", "Creator testimonial"),
        _tag("hook_verbatim", "text", "I was wrong"),
    ]
    big = await _creative(db, org_id, aid, "big", "sha_a", "900", v2)
    small = await _creative(
        db,
        org_id,
        aid,
        "small",
        "sha_a",
        "100",
        [_tag("messenger", "single", "expert_on_camera"), _tag("duration_s", "numeric", "60")],
    )
    await _creative(
        db,
        org_id,
        aid,
        "other",
        "sha_b",
        "500",
        [_tag("messenger", "single", "graphic_only"), _tag("duration_s", "numeric", "20")],
    )
    await _creative(db, org_id, aid, "untagged", "sha_c", "500", None)

    units, edges = await build_units(db, org_id, aid)
    by = {u.sha256: u for u in units}
    assert set(by) == {"sha_a", "sha_b"}  # untagged file excluded, copies pooled
    a = by["sha_a"]
    assert set(a.creative_ids) == {big, small} and a.rep_creative_id == big
    assert a.spend == Decimal(2000) and a.impressions == 40_000
    assert a.purchases == 40 and a.plays == 32_000
    assert a.has("messenger", "creator_on_camera")
    assert not a.has("messenger", "expert_on_camera")
    assert a.concept_summary == "Creator testimonial" and a.hook_verbatim == "I was wrong"
    assert a.first_day == date(2026, 6, 1) and a.last_day == date(2026, 6, 2)
    assert a.duration_s == 60.0
    assert edges == {}  # fewer than 6 units: no terciles
