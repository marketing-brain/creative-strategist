from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from cs.core import queue
from cs.insights import jobs as insight_jobs
from cs.insights.explain import ClaudeExplainer
from cs.insights.fakes import FakeExplainer
from cs.insights.types import InsightDraft
from cs.jobs.worker import run_once
from tests.insights.seed import seed_account, seed_creative


async def test_compute_patterns_job_persists_with_explanations(db, blobs, settings, org_id, monkeypatch):
    fake = FakeExplainer()
    monkeypatch.setattr(insight_jobs, "EXPLAINER_FACTORY", lambda s: fake)
    acct = await seed_account(db, org_id, settings)
    today = date.today()
    days = [today - timedelta(days=i + 1) for i in range(10)]
    for i in range(6):
        await seed_creative(db, org_id, acct, f"q{i}", "image", {"hook_type": "question"},
                            [(d, Decimal("30"), 1000, 30, Decimal("1"), Decimal("50")) for d in days])
    for i in range(6):
        await seed_creative(db, org_id, acct, f"s{i}", "image", {"hook_type": "statistic"},
                            [(d, Decimal("30"), 1000, 10, Decimal("1"), Decimal("50")) for d in days])
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await run_once(db, blobs, settings, "w")
    n = await db.fetchval("SELECT count(*) FROM insight WHERE kind='pattern'")
    assert n >= 2 and len(fake.seen) == n
    assert await db.fetchval("SELECT explanation FROM insight LIMIT 1") == "fake explanation"
    # second run same window: no duplicates, no new explain calls
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM insight") == n and len(fake.seen) == n


async def test_schedules_enqueue_per_active_account(db, settings, org_id):
    await seed_account(db, org_id, settings)
    await insight_jobs.nightly_patterns(db, settings)
    await insight_jobs.hourly_fatigue(db, settings)
    types = sorted(r["job_type"] for r in await db.fetch("SELECT job_type FROM job"))
    assert types == ["insights.compute_fatigue", "insights.compute_patterns"]


async def test_claude_explainer_parses_structured_output(settings):
    class M:
        async def create(self, **kw):
            return SimpleNamespace(stop_reason="end_turn", model="m",
                                   content=[SimpleNamespace(type="text", text='{"explanation": "E", "suggested_test": "T"}')])
    client = SimpleNamespace(beta=SimpleNamespace(messages=M()))
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1"), 0.02, 0.03, 0.5)
    assert await ClaudeExplainer(settings, client=client).explain(d, {"dimension_description": "x", "account_name": "A"}) == ("E", "T")
