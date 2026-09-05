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


async def _org_id(client) -> str:
    r = await client.get("/me", headers=dev_headers())
    assert r.status_code == 200
    return r.json()["org_id"]


async def test_blob_route_serves_local_file(client, blobs):
    org = await _org_id(client)
    await blobs.put(f"{org}/x.txt", b"data", "text/plain")
    r = await client.get(f"/blobs/{org}/x.txt", headers=dev_headers())
    assert r.status_code == 200 and r.content == b"data"


async def test_blob_route_requires_auth(client, blobs):
    org = await _org_id(client)
    await blobs.put(f"{org}/x.txt", b"data", "text/plain")
    assert (await client.get(f"/blobs/{org}/x.txt")).status_code == 401


async def test_blob_route_hides_other_orgs(client, blobs):
    other = await _org_id(client)  # provisioned for org_test
    r = await client.get("/me", headers=dev_headers(org="org_other", user="user_2"))
    assert r.json()["org_id"] != other
    await blobs.put(f"{other}/secret.txt", b"data", "text/plain")
    r = await client.get(
        f"/blobs/{other}/secret.txt", headers=dev_headers(org="org_other", user="user_2")
    )
    assert r.status_code == 404


async def test_blob_route_rejects_traversal(client):
    org = await _org_id(client)
    # %2E%2E survives httpx's URL normalisation and reaches the route as "..".
    r = await client.get(f"/blobs/{org}/%2E%2E/escape.txt", headers=dev_headers())
    assert r.status_code == 400


async def test_blob_route_404_for_missing_key(client):
    org = await _org_id(client)
    r = await client.get(f"/blobs/{org}/nope.txt", headers=dev_headers())
    assert r.status_code == 404
