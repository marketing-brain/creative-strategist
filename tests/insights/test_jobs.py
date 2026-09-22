from cs.insights import jobs as insight_jobs
from tests.insights.seed import seed_account


async def test_schedules_enqueue_per_active_account(db, settings, org_id):
    await seed_account(db, org_id, settings)
    await insight_jobs.periodic_fatigue(db, settings)
    types = sorted(r["job_type"] for r in await db.fetch("SELECT job_type FROM job"))
    assert types == ["insights.compute_fatigue"]
