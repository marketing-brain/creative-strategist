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
    body = r.json()
    assert r.status_code == 200 and body["total"] == 1
    assert body["items"][0]["lift"] == 0.5 and body["items"][0]["explanation"] == "E"
    fatigue = (await client.get("/fatigue", headers=H)).json()
    assert fatigue == {"items": [], "total": 0}
    briefs = (await client.get("/briefs", headers=H)).json()
    assert briefs[0]["content_md"] == "# B"
    assert (await client.get(f"/briefs/{briefs[0]['id']}", headers=H)).status_code == 200
    other_org = (await client.get("/insights", headers=dev_headers(org="org_x"))).json()
    assert other_org == {"items": [], "total": 0}


async def test_insights_pagination(client, db):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    for i in range(3):
        d = InsightDraft(
            "pattern",
            "hook_type",
            f"value_{i}",
            "ctr",
            date(2026, 8, 1),
            date(2026, 8, 31),
            6,
            Decimal("1500"),
            0.02,
            0.03,
            0.5 + i,
        )
        await repo.insert_insight(db, org_id, d, "E", "T")

    page1 = (await client.get("/insights?kind=pattern&limit=2&offset=0", headers=H)).json()
    assert page1["total"] == 3 and len(page1["items"]) == 2

    page2 = (await client.get("/insights?kind=pattern&limit=2&offset=2", headers=H)).json()
    assert page2["total"] == 3 and len(page2["items"]) == 1

    page1_ids = {i["id"] for i in page1["items"]}
    page2_ids = {i["id"] for i in page2["items"]}
    assert not page1_ids & page2_ids
