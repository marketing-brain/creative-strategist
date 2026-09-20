import json
from uuid import UUID

from cs.core.ids import new_id
from cs.insights.account_read import ask, repo
from cs.insights.fakes import FakeAccountReadWriter
from tests.conftest import dev_headers

H = dev_headers(org="org_ar")


async def _account(client, db):
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    aid = new_id()
    await db.execute(
        "INSERT INTO ad_account (id, org_id, platform, platform_account_id, name, status) "
        "VALUES ($1,$2,'meta','act_ar','A','active')",
        aid,
        org_id,
    )
    return org_id, aid


def _test(**over):
    base = {
        "dimension": "messenger",
        "value": "expert_on_camera",
        "group": None,
        "metric": "link_ctr",
        "kind": "per_unit",
        "n_cell": 6,
        "n_complement": 6,
        "cell_value": 0.04,
        "comp_value": 0.03,
        "change": 0.33,
        "p": 0.02,
        "grade": "suggestive",
        "confounds": [],
        "cell_shas": ["a"],
        "bin_edges": None,
        "cell_purchases": None,
    }
    base.update(over)
    return base


async def test_account_read_states_and_cross_org(client, db):
    org_id, aid = await _account(client, db)
    r = await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)
    assert r.status_code == 200 and r.json() == {"status": "none"}
    await repo.insert_run(
        db,
        org_id,
        aid,
        status="insufficient",
        n_units=4,
        result={"summary": {"units": 4}},
        read=None,
        error=None,
    )
    body = (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)).json()
    assert body["status"] == "insufficient" and body["n_units"] == 4
    result = {
        "summary": {"units": 12},
        "tests": [
            _test(),
            _test(
                dimension="angles",
                value="humor",
                metric="cpa",
                kind="pooled",
                cell_value=40,
                comp_value=41,
                change=-0.02,
                p=0.9,
                grade="thin",
                cell_shas=["b"],
                cell_purchases=30,
            ),
        ],
        "units": [
            {
                "sha256": "a",
                "headline": "H",
                "spend": 100,
                "creative_ids": [],
                "rep_creative_id": None,
            }
        ],
        "overlaps": [],
        "stratified": [],
        "retention_by_reveal": {},
    }
    await repo.insert_run(
        db,
        org_id,
        aid,
        status="done",
        n_units=12,
        result=result,
        read={"overview": "o", "findings": [{"pattern_ids": ["P2"], "paragraph": "Humor (P2)."}]},
        error=None,
    )
    body = (await client.get(f"/insights/account-read?ad_account_id={aid}", headers=H)).json()
    assert body["status"] == "done" and body["read"]["overview"] == "o"
    # only what the read cites crosses the wire, tagged with its place in the run's full list
    assert [(t["pid"], t["grade"]) for t in body["tests"]] == [("P2", "thin")]
    assert body["sibling_tests"] == [] and "stratified" not in body
    assert body["units"][0]["sha256"] == "a" and body["units"][0]["poster_url"] is None
    other = await client.get(
        f"/insights/account-read?ad_account_id={aid}", headers=dev_headers(org="org_other")
    )
    assert other.status_code == 404


async def test_ask_route(client, db, monkeypatch):
    org_id, aid = await _account(client, db)
    await repo.insert_run(
        db,
        org_id,
        aid,
        status="done",
        n_units=12,
        result={"summary": {"units": 12, "dimensions": []}, "tests": [], "units": []},
        read={"overview": "o", "findings": []},
        error=None,
    )
    monkeypatch.setattr(
        ask,
        "ASK_WRITER_FACTORY",
        lambda s: FakeAccountReadWriter([json.dumps({"unmappable": "persona is not tagged"})]),
    )
    r = await client.post(
        "/insights/account-read/ask",
        json={"ad_account_id": str(aid), "question": "trust per persona"},
        headers=H,
    )
    assert r.status_code == 200 and r.json() == {"unmappable": "persona is not tagged"}
    r = await client.post(
        "/insights/account-read/ask",
        json={"ad_account_id": str(aid), "question": "x"},
        headers=dev_headers(org="org_other"),
    )
    assert r.status_code == 404
