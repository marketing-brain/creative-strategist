from datetime import date
from decimal import Decimal

from cs.insights import repo
from cs.insights.types import InsightDraft
from tests.conftest import dev_headers

H = dev_headers(org="org_ins")


async def test_insights_and_briefs_routes(client, db):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    d = InsightDraft(
        "pattern",
        "hook_type",
        "question",
        "ctr",
        date(2026, 8, 1),
        date(2026, 8, 31),
        6,
        Decimal("1500"),
        0.02,
        0.03,
        0.5,
    )
    await repo.insert_insight(db, org_id, d, "E", "T")
    await repo.insert_brief(db, org_id, date(2026, 8, 31), "# B", [])
    r = await client.get("/insights?kind=pattern", headers=H)
    assert r.status_code == 200 and r.json()[0]["lift"] == 0.5 and r.json()[0]["explanation"] == "E"
    assert (await client.get("/fatigue", headers=H)).json() == []
    briefs = (await client.get("/briefs", headers=H)).json()
    assert briefs[0]["content_md"] == "# B"
    assert (await client.get(f"/briefs/{briefs[0]['id']}", headers=H)).status_code == 200
    assert (await client.get("/insights", headers=dev_headers(org="org_x"))).json() == []
