from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request

from cs.api.deps import get_db, get_settings_dep
from cs.core.config import Settings
from cs.core.db import Database
from cs.orgs.repo import ensure_membership, get_or_create_org

log = logging.getLogger("cs.auth")


@dataclass
class AuthContext:
    user_id: str
    org_id: UUID
    role: str


@lru_cache
def _jwks_client(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(url)


CLOCK_LEEWAY = timedelta(seconds=60)
_AUTH_TTL_S = 600
_ORG_CACHE: dict[str, tuple] = {}
_MEMBER_CACHE: dict[tuple, float] = {}


def _clerk_claims(request: Request, settings: Settings) -> tuple[str, str, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = header.split(" ", 1)[1]
    try:
        key = _jwks_client(settings.clerk_jwks_url).get_signing_key_from_jwt(token).key
        # Clerk mints a token on its clock and this box validates on its own. A second of
        # drift -- routine after a sleep/wake or a date rollover -- made every request fail
        # with "The token is not yet valid (iat)" until the clocks converged, which looks to
        # the user like the whole app is down. The tolerance is small enough that it changes
        # nothing about expiry in practice.
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            issuer=settings.clerk_issuer or None,
            leeway=CLOCK_LEEWAY,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - JWKS fetch failures are not the caller's fault
        log.warning("jwks lookup failed: %s", exc)
        raise HTTPException(503, "auth provider unavailable") from exc
    # Session token v2 (Clerk's default since 2025-04-14) nests organization
    # claims under "o" (o.id, o.rol) instead of flat org_id/org_role -- verified
    # against a real token, not assumed from docs. Fall back to the v1 flat
    # claims for any instance still emitting the deprecated format.
    org_claim = claims.get("o") or {}
    org = org_claim.get("id") or claims.get("org_id")
    if not org:
        raise HTTPException(403, "no active organization")
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(401, "token missing sub")
    org_role = org_claim.get("rol") or claims.get("org_role", "")
    role = "admin" if str(org_role).endswith("admin") else "member"
    return sub, org, role


async def current_auth(
    request: Request,
    db: Database = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    user_id, clerk_org, role = _clerk_claims(request, settings)
    # The org row and the membership only change when someone joins or their role changes, so
    # they are written once and remembered: doing both on every request cost two database
    # round trips (one a write) per API call, about 134 ms from a laptop (2026-09-26).
    now = time.monotonic()
    org_id = _ORG_CACHE.get(clerk_org)
    if org_id is None or now - org_id[1] > _AUTH_TTL_S:
        org_id = (await get_or_create_org(db, clerk_org, name=clerk_org), now)
        _ORG_CACHE[clerk_org] = org_id
    member_key = (user_id, org_id[0], role)
    if now - _MEMBER_CACHE.get(member_key, -_AUTH_TTL_S - 1) > _AUTH_TTL_S:
        await ensure_membership(db, org_id[0], user_id, role)
        _MEMBER_CACHE[member_key] = now
    return AuthContext(user_id=user_id, org_id=org_id[0], role=role)


async def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.role != "admin":
        raise HTTPException(403, "admin required")
    return auth
