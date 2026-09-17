from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.brief import render_brief
from cs.insights.types import InsightDraft


async def test_render_brief_lists_fatigue(db, org_id):
    d = InsightDraft(
        "fatigue",
        "creative",
        "c1",
        "ctr",
        date(2026, 8, 1),
        date(2026, 8, 31),
        1,
        Decimal("1500"),
        0.03,
        0.02,
        -0.33,
    )
    await repo.insert_insight(db, org_id, d, "Wearing out", "Rotate a fresh hook")
    fatigue = await repo.list_insights(db, org_id, "fatigue")
    md = render_brief(date(2026, 8, 31), fatigue)
    assert md.startswith("# Weekly creative brief")
    assert "-33%" in md and "Rotate a fresh hook" in md
    assert "What is working" not in md
    assert "No fatigue alerts" in render_brief(date(2026, 8, 31), [])
