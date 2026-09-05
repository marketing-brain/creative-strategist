from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.types import InsightDraft


def _draft(**kw):
    base = dict(kind="pattern", dimension="hook_type", value="question", metric="ctr",
                window_start=date(2026, 8, 1), window_end=date(2026, 8, 31), sample_creatives=6,
                sample_spend=Decimal("1500"), baseline=0.01, observed=0.013, lift=0.3)
    base.update(kw)
    return InsightDraft(**base)


async def test_insert_skips_duplicates_and_lists(db, org_id):
    a = await repo.insert_insight(db, org_id, _draft(), "because", "test X")
    b = await repo.insert_insight(db, org_id, _draft(), "because", "test X")
    assert a is not None and b is None
    rows = await repo.list_insights(db, org_id, "pattern")
    assert len(rows) == 1 and rows[0]["explanation"] == "because"
    assert await repo.list_insights(db, org_id, "fatigue") == []


async def test_briefs(db, org_id):
    bid = await repo.insert_brief(db, org_id, date(2026, 8, 31), "# Brief", [])
    assert bid is not None
    assert (await repo.list_briefs(db, org_id))[0]["content_md"] == "# Brief"
