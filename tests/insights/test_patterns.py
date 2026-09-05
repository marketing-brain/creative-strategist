from datetime import date, timedelta
from decimal import Decimal

from cs.insights.patterns import compute_pattern_insights, metrics_from_sums, signed_lift
from tests.insights.seed import seed_account, seed_creative


def test_metrics_and_lift():
    m = metrics_from_sums(Decimal("100"), 10000, 200, Decimal("10"), Decimal("500"))
    assert m == {"ctr": 0.02, "cpa": 10.0, "roas": 5.0}
    assert metrics_from_sums(Decimal("0"), 0, 0, Decimal("0"), Decimal("0")) == {
        "ctr": None,
        "cpa": None,
        "roas": None,
    }
    assert signed_lift("ctr", 0.02, 0.01) == 1.0
    assert signed_lift("cpa", 5.0, 10.0) == 0.5
    assert signed_lift("cpa", None, 10.0) is None


async def test_pattern_surfaces_high_ctr_hook(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    start = date(2026, 8, 1)
    days = [start + timedelta(days=i) for i in range(10)]
    for i in range(6):  # question hooks: ctr 3%
        await seed_creative(
            db,
            org_id,
            acct,
            f"q{i}",
            "image",
            {"hook_type": "question"},
            [(d, Decimal("30"), 1000, 30, Decimal("1"), Decimal("50")) for d in days],
        )
    for i in range(6):  # statistic hooks: ctr 1%
        await seed_creative(
            db,
            org_id,
            acct,
            f"s{i}",
            "image",
            {"hook_type": "statistic"},
            [(d, Decimal("30"), 1000, 10, Decimal("1"), Decimal("50")) for d in days],
        )
    drafts = await compute_pattern_insights(db, org_id, acct, start, start + timedelta(days=30))
    by = {(d.dimension, d.value, d.metric): d for d in drafts}
    q = by[("hook_type", "question", "ctr")]
    assert q.sample_creatives == 6 and q.sample_spend == Decimal("1800")
    assert round(q.observed, 3) == 0.03 and round(q.baseline, 3) == 0.02 and round(q.lift, 2) == 0.5
    s = by[("hook_type", "statistic", "ctr")]
    assert s.lift < 0
    assert ("hook_type", "question", "cpa") not in by  # same cpa, no lift


async def test_pattern_respects_thresholds(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    start = date(2026, 8, 1)
    for i in range(3):
        await seed_creative(
            db,
            org_id,
            acct,
            f"q{i}",
            "image",
            {"hook_type": "question"},
            [(start, Decimal("10"), 100, 5, Decimal("0"), Decimal("0"))],
        )
    assert await compute_pattern_insights(db, org_id, acct, start, start + timedelta(days=30)) == []
