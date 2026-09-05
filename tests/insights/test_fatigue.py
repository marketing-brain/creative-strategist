from datetime import date, timedelta
from decimal import Decimal

from cs.insights.fatigue import compute_fatigue
from tests.insights.seed import seed_account, seed_creative


async def test_fatigue_flags_ctr_decay(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 4)
    start = today - timedelta(days=40)
    good = [
        (start + timedelta(days=i), Decimal("20"), 1000, 30, Decimal("2"), Decimal("100"))
        for i in range(14)
    ]
    bad = [
        (today - timedelta(days=i), Decimal("20"), 1000, 15, Decimal("2"), Decimal("100"))
        for i in range(7)
    ]
    fatigued = await seed_creative(
        db, org_id, acct, "f", "image", {"hook_type": "question"}, good + bad
    )
    steady = [
        (start + timedelta(days=i), Decimal("20"), 1000, 30, Decimal("2"), Decimal("100"))
        for i in range(40)
    ]
    await seed_creative(db, org_id, acct, "ok", "image", {"hook_type": "question"}, steady)
    drafts = await compute_fatigue(db, org_id, acct, today)
    assert [(d.creative_id, d.metric) for d in drafts] == [(fatigued, "ctr")]
    d = drafts[0]
    assert (
        round(d.baseline, 3) == 0.03 and round(d.observed, 3) == 0.015 and round(d.lift, 2) == -0.5
    )
    assert d.window_start == today - timedelta(days=6) and d.window_end == today


async def test_fatigue_ignores_low_spend(db, org_id, settings):
    acct = await seed_account(db, org_id, settings)
    today = date(2026, 9, 4)
    rows = [
        (today - timedelta(days=30 - i), Decimal("1"), 100, 5, Decimal("0"), Decimal("0"))
        for i in range(31)
    ]
    await seed_creative(db, org_id, acct, "tiny", "image", {}, rows)
    assert await compute_fatigue(db, org_id, acct, today) == []
