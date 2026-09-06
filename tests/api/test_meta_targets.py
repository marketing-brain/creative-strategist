from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.ingest import service as ingest_service
from tests.conftest import dev_headers

H = dev_headers(org="org_targets")


async def test_adsets_rejects_non_numeric_campaign_id(client, db, settings):
    """Regression test: campaign_id is passed straight into a Graph API URL path
    (client.get_all(f"{campaign_id}/adsets", ...)) -- an arbitrary string here would let a
    caller make the app issue an arbitrary Graph API GET using the org's token. Meta node ids
    are always numeric, so anything else must be rejected before it reaches the connector."""
    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
    )
    r = await client.get(
        f"/accounts/{acct}/adsets", headers=H, params={"campaign_id": "not-a-number"}
    )
    assert r.status_code == 422


async def test_adsets_accepts_numeric_campaign_id(client, db, settings, monkeypatch):
    from cs.connectors.fakes import FakeConnector

    org_id = (await client.get("/me", headers=H)).json()["org_id"]
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
    )
    fake = FakeConnector()
    monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)
    r = await client.get(f"/accounts/{acct}/adsets", headers=H, params={"campaign_id": "12345"})
    assert r.status_code == 200 and r.json()[0]["id"] == "as1"
