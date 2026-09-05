import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends

from cs.api import auth as auth_mod
from cs.api.app import create_app

JWKS_URL = "https://example.test/jwks"
ISSUER = "https://issuer.test"


def _rsa_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture(scope="session")
def keypair():
    return _rsa_pair()


@pytest.fixture(scope="session")
def other_keypair():
    return _rsa_pair()


class _FakeSigningKey:
    def __init__(self, key: str):
        self.key = key


class _FakeJWKSClient:
    def __init__(self, key: str):
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return _FakeSigningKey(self._key)


@pytest.fixture
def clerk_settings(settings):
    return settings.model_copy(
        update={"auth_mode": "clerk", "clerk_jwks_url": JWKS_URL, "clerk_issuer": ISSUER}
    )


@pytest.fixture
def jwks(monkeypatch, keypair):
    """Replace the cached JWKS client with one that always returns our public key."""
    real_client = auth_mod._jwks_client
    real_client.cache_clear()
    holder = {"client": _FakeJWKSClient(keypair[1])}
    monkeypatch.setattr(auth_mod, "_jwks_client", lambda url: holder["client"])
    yield holder
    real_client.cache_clear()


@pytest.fixture
async def clerk_client(clerk_settings, db, blobs, jwks):
    app = create_app(clerk_settings, db, blobs)

    async def admin_only(a: auth_mod.AuthContext = Depends(auth_mod.require_admin)) -> dict:
        return {"ok": True, "role": a.role}

    app.add_api_route("/_test/admin-only", admin_only, methods=["GET"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def make_token(private_pem: str, **claims) -> str:
    now = int(time.time())
    payload = {"sub": "user_clerk", "iss": ISSUER, "iat": now, "exp": now + 300}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, private_pem, algorithm="RS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_valid_admin_token(clerk_client, keypair, db):
    token = make_token(keypair[0], org_id="org_clerk", org_role="org:admin")
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "user_clerk" and body["role"] == "admin"
    assert await db.fetchval("SELECT count(*) FROM org WHERE clerk_org_id='org_clerk'") == 1


async def test_member_role(clerk_client, keypair):
    token = make_token(keypair[0], org_id="org_clerk", org_role="org:member")
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 200 and r.json()["role"] == "member"


async def test_missing_org_is_forbidden(clerk_client, keypair):
    r = await clerk_client.get("/me", headers=bearer(make_token(keypair[0])))
    assert r.status_code == 403


async def test_bad_signature_is_unauthorized(clerk_client, other_keypair):
    token = make_token(other_keypair[0], org_id="org_clerk", org_role="org:admin")
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 401


async def test_missing_sub_is_unauthorized(clerk_client, keypair):
    token = make_token(keypair[0], sub=None, org_id="org_clerk", org_role="org:admin")
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 401 and "sub" in r.json()["detail"]


async def test_expired_token_is_unauthorized(clerk_client, keypair):
    now = int(time.time())
    token = make_token(keypair[0], org_id="org_clerk", exp=now - 10, iat=now - 100)
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 401


async def test_missing_bearer_header_is_unauthorized(clerk_client):
    assert (await clerk_client.get("/me")).status_code == 401


async def test_jwks_fetch_failure_is_service_unavailable(clerk_client, keypair, jwks):
    class Broken:
        def get_signing_key_from_jwt(self, token):
            raise OSError("jwks unreachable")

    jwks["client"] = Broken()
    token = make_token(keypair[0], org_id="org_clerk", org_role="org:admin")
    r = await clerk_client.get("/me", headers=bearer(token))
    assert r.status_code == 503 and r.json()["detail"] == "auth provider unavailable"


async def test_require_admin_rejects_member(clerk_client, keypair):
    member = make_token(keypair[0], org_id="org_clerk", org_role="org:member")
    assert (await clerk_client.get("/_test/admin-only", headers=bearer(member))).status_code == 403
    admin = make_token(keypair[0], org_id="org_clerk", org_role="org:admin")
    assert (await clerk_client.get("/_test/admin-only", headers=bearer(admin))).status_code == 200
