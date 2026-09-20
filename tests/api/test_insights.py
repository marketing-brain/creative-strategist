from tests.conftest import dev_headers

H = dev_headers(org="org_ins")


async def test_fatigue_route(client):
    assert (await client.get("/fatigue", headers=H)).json() == {"items": [], "total": 0}


async def test_brief_routes_are_gone(client):
    assert (await client.get("/briefs", headers=H)).status_code in (404, 405)


async def test_old_pattern_insights_route_is_gone(client):
    assert (await client.get("/insights?kind=pattern", headers=H)).status_code in (404, 405)
