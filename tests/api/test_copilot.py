from cs.api.routers import copilot as copilot_router
from cs.insights.fakes import FakeCopilot
from tests.conftest import dev_headers

H = dev_headers(org="org_cp", user="u1")


async def test_copilot_roundtrip(client, db, monkeypatch):
    monkeypatch.setattr(copilot_router, "COPILOT_FACTORY", lambda s: FakeCopilot())
    conv = (await client.post("/copilot/conversations", headers=H, json={"title": "Q"})).json()[
        "id"
    ]
    r = await client.post(
        f"/copilot/conversations/{conv}/messages", headers=H, json={"content": "what works?"}
    )
    assert r.status_code == 200 and r.json()["reply"] == "fake answer (0 insights)"
    msgs = (await client.get(f"/copilot/conversations/{conv}", headers=H)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    other = await client.get("/copilot/conversations", headers=dev_headers(org="org_cp", user="u2"))
    assert other.json() == []


async def test_conversation_is_scoped_to_owning_user(client, db, monkeypatch):
    monkeypatch.setattr(copilot_router, "COPILOT_FACTORY", lambda s: FakeCopilot())
    conv = (await client.post("/copilot/conversations", headers=H, json={"title": "Q"})).json()[
        "id"
    ]
    other = dev_headers(org="org_cp", user="u2")
    assert (await client.get(f"/copilot/conversations/{conv}", headers=other)).status_code == 404
    assert (
        await client.post(
            f"/copilot/conversations/{conv}/messages", headers=other, json={"content": "hi"}
        )
    ).status_code == 404
