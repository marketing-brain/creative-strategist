from tests.conftest import dev_headers


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


async def test_me_requires_auth(client):
    assert (await client.get("/me")).status_code == 401


async def test_me_provisions_org_and_membership(client, db):
    r = await client.get("/me", headers=dev_headers(org="org_abc", user="user_9"))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user_9" and body["role"] == "admin"
    org = await db.fetchrow("SELECT id FROM org WHERE clerk_org_id='org_abc'")
    assert str(org["id"]) == body["org_id"]
    assert await db.fetchval("SELECT count(*) FROM user_org WHERE user_id='user_9'") == 1


async def test_blob_route_serves_local_file(client, blobs):
    await blobs.put("o/x.txt", b"data", "text/plain")
    r = await client.get("/blobs/o/x.txt")
    assert r.status_code == 200 and r.content == b"data"
