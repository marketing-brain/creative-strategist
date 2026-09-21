from uuid import UUID

from cs.core.ids import new_id
from cs.insights.next_plan import repo
from tests.conftest import dev_headers

H = dev_headers(org="org_next")


async def _account(client, db):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    aid = new_id()
    await db.execute(
        "INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, status) "
        "VALUES ($1,$2,'meta','act_next','A','active')",
        aid,
        org_id,
    )
    return org_id, aid


async def test_no_plan_yet_reads_as_none(client, db):
    _, aid = await _account(client, db)
    r = await client.get(f"/insights/next?ad_account_id={aid}", headers=H)
    assert r.status_code == 200
    assert r.json() == {"status": "none"}


async def test_the_plan_is_served_whole(client, db):
    org_id, aid = await _account(client, db)
    plan = {
        "account_spend": 1000.0,
        "n_units": 12,
        "allocate": [
            {
                "key": "fix:a",
                "kind": "fix",
                "creative_id": str(new_id()),
                "headline": "Ad one",
                "rep_creative_id": None,
                "stake": {"amount": 500.0, "share": 0.5, "window": "30d", "basis": "b"},
                "verdict": "Broken layer: Attention.",
                "detail": "d",
                "broken_layer": "Attention",
            }
        ],
        "make": [],
        "explore": [{"key": "explore:x", "dimension": "angles", "value": "identity"}],
        "next_tests": ["Vary the opening."],
    }
    await repo.insert_run(db, org_id, aid, status="done", plan=plan, account_read_run_id=None)
    body = (await client.get(f"/insights/next?ad_account_id={aid}", headers=H)).json()
    assert body["status"] == "done"
    assert body["n_units"] == 12
    assert body["allocate"][0]["kind"] == "fix"
    # No poster asset exists, so the row still carries the key rather than going missing.
    assert body["allocate"][0]["poster_url"] is None
    assert body["explore"][0]["value"] == "identity"
    assert body["next_tests"] == ["Vary the opening."]
    assert body["created_at"]


async def test_another_org_cannot_read_this_plan(client, db):
    org_id, aid = await _account(client, db)
    await repo.insert_run(
        db, org_id, aid, status="done", plan={"n_units": 1}, account_read_run_id=None
    )
    r = await client.get(
        f"/insights/next?ad_account_id={aid}", headers=dev_headers(org="org_next_other")
    )
    assert r.status_code == 404
