from tests.conftest import dev_headers

H = dev_headers(org="org_ins")


async def test_fatigue_route_is_gone(client):
    """The tab was removed on 2026-09-20: its cards restated what /next's Replace block shows,
    and every card carried a "Next test" -- a recommendation on a page that does not own them.
    `compute_fatigue` and the insight rows stay; /next reads them."""
    assert (await client.get("/fatigue", headers=H)).status_code in (404, 405)


async def test_brief_routes_are_gone(client):
    assert (await client.get("/briefs", headers=H)).status_code in (404, 405)


async def test_old_pattern_insights_route_is_gone(client):
    assert (await client.get("/insights?kind=pattern", headers=H)).status_code in (404, 405)
