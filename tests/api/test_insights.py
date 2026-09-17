from datetime import date

from cs.insights import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_ins")


async def test_fatigue_and_briefs_routes(client, db):
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    await repo.insert_brief(db, org_id, date(2026, 8, 31), "# B", [])
    fatigue = (await client.get("/fatigue", headers=H)).json()
    assert fatigue == {"items": [], "total": 0}
    briefs = (await client.get("/briefs", headers=H)).json()
    assert briefs[0]["content_md"] == "# B"
    assert (await client.get(f"/briefs/{briefs[0]['id']}", headers=H)).status_code == 200
    other_org = (await client.get("/briefs", headers=dev_headers(org="org_x"))).json()
    assert other_org == []


async def test_old_pattern_insights_route_is_gone(client):
    assert (await client.get("/insights?kind=pattern", headers=H)).status_code in (404, 405)
