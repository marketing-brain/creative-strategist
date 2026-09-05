from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.brief import render_brief
from cs.insights.types import InsightDraft


async def test_render_brief_lists_patterns_and_fatigue(db, org_id):
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1500"), 0.02, 0.03, 0.5)
    await repo.insert_insight(db, org_id, d, "Questions win", "Test 2 more")
    patterns = await repo.list_insights(db, org_id, "pattern")
    md = render_brief(date(2026, 8, 31), patterns, [])
    assert md.startswith("# Weekly creative brief") and "hook_type = question" in md and "+50%" in md and "Test 2 more" in md
    assert "No fatigue alerts" in md
