from __future__ import annotations

import re
from uuid import UUID

import httpx

from cs.connectors.fivetran import client as fivetran_client
from cs.core.config import Settings

SERVICE = "facebook_ads"


class ConnectError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def schema_for(org_id: UUID) -> str:
    """org_{org_slug}__meta_ads -- the Postgres schema Fivetran lands this org's data into.

    Exactly one implementation in the whole app: the transform (fivetran/transform.py)
    imports this rather than re-deriving it, since drift here silently reads the wrong
    (or no) schema. The "org_" prefix is required, not cosmetic: Fivetran rejects a
    connector schema that begins with a digit, and org_id is a UUID -- roughly 5/8 of
    UUIDs start with a hex digit 0-9, so an unprefixed slug fails against the real API
    for most orgs (caught live against a real Fivetran connector, not by the fixture
    tests, which never exercised this constraint).
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(org_id).lower()).strip("_")
    return f"org_{slug}__meta_ads"


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.web_base_url.rstrip('/')}/fivetran-return"


async def start_connection(settings: Settings, http: httpx.AsyncClient, org_id: UUID) -> dict:
    if not settings.fivetran_group_id:
        raise ConnectError(500, "FIVETRAN_GROUP_ID not configured")
    schema = schema_for(org_id)
    # Fivetran rejects a duplicate schema. Deleting the old connector to make room is a
    # destructive, irreversible action against a connection that may be healthy and
    # actively syncing -- it must never happen as a side effect of starting a new one.
    # Callers hit this 409 and must explicitly call disconnect_connection first.
    existing = await fivetran_client.find_connector_by_schema(
        settings, http, settings.fivetran_group_id, schema
    )
    if existing:
        raise ConnectError(409, "A Meta connection already exists -- disconnect it first.")
    connector = await fivetran_client.create_connector(
        settings,
        http,
        service=SERVICE,
        group_id=settings.fivetran_group_id,
        schema=schema,
        redirect_uri=_redirect_uri(settings),
    )
    card = connector.get("connect_card") or {}
    if not card.get("uri"):
        raise ConnectError(502, "Fivetran returned no Connect Card URL")
    return {"connect_card_url": card["uri"], "connector_id": connector["id"]}


async def finalize_connection(
    settings: Settings, http: httpx.AsyncClient, org_id: UUID, connector_id: str
) -> dict:
    connector = await fivetran_client.get_connector(settings, http, connector_id)
    if connector.get("schema") != schema_for(org_id):
        raise ConnectError(403, "connection does not belong to you")
    return connector


async def disconnect_connection(settings: Settings, http: httpx.AsyncClient, org_id: UUID) -> None:
    if not settings.fivetran_group_id:
        raise ConnectError(500, "FIVETRAN_GROUP_ID not configured")
    schema = schema_for(org_id)
    existing = await fivetran_client.find_connector_by_schema(
        settings, http, settings.fivetran_group_id, schema
    )
    if not existing:
        raise ConnectError(404, "No Meta connection exists for this organization.")
    await fivetran_client.delete_connector(settings, http, existing)
