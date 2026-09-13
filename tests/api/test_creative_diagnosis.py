from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_diag")
H_OTHER = dev_headers(org="org_diag_other")


def _row(
    ad: str,
    day: date,
    *,
    imp: int,
    views3: int,
    plays: int,
    thru: int,
    p25: int,
    p50: int,
    reach: int,
) -> MetricRow:
    return MetricRow(
        ad,
        day,
        "all",
        Decimal("10"),
        imp,
        imp // 100,
        imp // 100,
        Decimal(1),
        Decimal(40),
        views3,
        thru,
        p50,
        p50 // 2,
        {},
        reach=reach,
        video_plays=plays,
        video_p25=p25,
        video_p75=p50 // 2,
        video_p95=p50 // 3,
        landing_page_views=imp // 150,
    )


async def _seed(db, client, *, today: date):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")

    async def creative(pid: str, fmt: str) -> UUID:
        cid, _ = await repo.upsert_creative(
            db,
            org_id,
            acct,
            PlatformCreative(pid, fmt, "H " + pid, "P", None, None, "u", None, None, {}),
            "raw",
        )
        await repo.upsert_ad(
            db, org_id, acct, PlatformAd("ad_" + pid, pid, "ACTIVE", None, None, pid), cid
        )
        return cid

    target = await creative("t", "video")
    aid = await repo.insert_asset(
        db, org_id, target, "original_video", "o/t.mp4", "sha", "video/mp4", duration_ms=24_000
    )
    await repo.set_primary_asset(db, org_id, target, aid)
    peer_video = await creative("pv", "video")
    peer_image = await creative("pi", "image")

    rows: list[MetricRow] = []
    for i in range(30):
        d = today - timedelta(days=i)
        # target: weak retention (p50 share 0.29 vs peers 0.54), strong hook.
        rows.append(
            _row("ad_t", d, imp=20_000, views3=7_600, plays=7_600, thru=1_600, p25=5_400,
                 p50=2_200, reach=8_000)
        )  # fmt: skip
        rows.append(
            _row("ad_pv", d, imp=20_000, views3=6_200, plays=6_200, thru=2_100, p25=4_650,
                 p50=3_350, reach=9_000)
        )  # fmt: skip
        rows.append(
            _row("ad_pi", d, imp=20_000, views3=0, plays=0, thru=0, p25=0, p50=0, reach=9_000)
        )  # fmt: skip
    # a row outside the window must not count
    old = today - timedelta(days=40)
    rows.append(
        _row("ad_t", old, imp=1_000_000, views3=0, plays=0, thru=0, p25=0, p50=0, reach=1)
    )  # fmt: skip
    await repo.upsert_metrics(db, org_id, acct, rows)
    return org_id, target, peer_video, peer_image


async def test_diagnosis_endpoint_uses_same_format_baseline_and_excludes_self(client, db):
    today = datetime.now(UTC).date()
    _, target, _, _ = await _seed(db, client, today=today)
    r = await client.get(f"/creatives/{target}/diagnosis", headers=H)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["format"] == "video" and d["baseline_creatives"] == 1  # only the video peer
    assert d["window_end"] == today.isoformat()
    assert d["window_start"] == (today - timedelta(days=29)).isoformat()
    tiers = {t["key"]: t for t in d["tiers"]}
    assert tiers["attention"]["status"] == "strong"
    assert tiers["retention"]["status"] == "weak"
    assert tiers["retention"]["curve"]["duration_s"] == 24.0
    assert tiers["retention"]["curve"]["drop_segment"] == [25, 50]
    assert tiers["context"]["status"] == "not_measured"
    assert d["broken_tier"] == 2 and d["verdict"]["kind"] == "broken"
    # 41 live days >= 21 -> fatigue is computed, not too_young
    assert tiers["fatigue"]["status"] in ("on_par", "weak")
    hook = next(m for m in tiers["attention"]["metrics"] if m["key"] == "hook_rate")
    assert abs(hook["value"] - 0.38) < 1e-6 and abs(hook["baseline"] - 0.31) < 1e-6


async def test_diagnosis_404s_for_unknown_and_cross_org(client, db):
    _, target, _, _ = await _seed(db, client, today=datetime.now(UTC).date())
    r = await client.get(f"/creatives/{target}/diagnosis", headers=H_OTHER)
    assert r.status_code == 404
    r = await client.get("/creatives/00000000-0000-0000-0000-000000000000/diagnosis", headers=H)
    assert r.status_code == 404


async def test_diagnosis_with_no_metrics_is_too_early(client, db):
    me = (await client.get("/me", headers=H)).json()
    org_id = UUID(me["org_id"])
    acct = await repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("x", "image", "H", "P", None, None, "u", None, None, {}),
        "raw",
    )
    d = (await client.get(f"/creatives/{cid}/diagnosis", headers=H)).json()
    assert d["verdict"]["kind"] == "too_early" and d["baseline_creatives"] == 0
    statuses = {t["status"] for t in d["tiers"]}
    assert statuses == {"not_enough_data", "not_applicable", "too_young", "not_measured"}
