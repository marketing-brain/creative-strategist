from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request

from cs.api.deps import get_db, get_settings_dep
from cs.core.config import Settings
from cs.core.db import Database
from cs.orgs.repo import ensure_membership, get_or_create_org


@dataclass
class AuthContext:
    user_id: str
    org_id: UUID
    role: str


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url)


def _clerk_claims(request: Request, settings: Settings) -> tuple[str, str, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = header.split(" ", 1)[1]
    try:
        key = _jwks_client(settings.clerk_jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=settings.clerk_issuer or None)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    org = claims.get("org_id")
    if not org:
        raise HTTPException(403, "no active organization")
    role = "admin" if str(claims.get("org_role", "")).endswith("admin") else "member"
    return claims["sub"], org, role


def _dev_claims(request: Request) -> tuple[str, str, str]:
    user = request.headers.get("x-dev-user")
    org = request.headers.get("x-dev-org")
    if not user or not org:
        raise HTTPException(401, "dev auth requires X-Dev-User and X-Dev-Org")
    return user, org, request.headers.get("x-dev-role", "admin")


async def current_auth(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    if settings.auth_mode == "clerk":
        user_id, clerk_org, role = _clerk_claims(request, settings)
    else:
        user_id, clerk_org, role = _dev_claims(request)
    org_id = await get_or_create_org(db, clerk_org, name=clerk_org)
    await ensure_membership(db, org_id, user_id, role)
    return AuthContext(user_id=user_id, org_id=org_id, role=role)


async def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth
