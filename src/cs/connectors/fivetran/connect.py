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
    """{org_slug}__meta_ads -- the Postgres schema Fivetran lands this org's data into.

    Exactly one implementation in the whole app: the transform (fivetran_transform.py)
    imports this rather than re-deriving it, since drift here silently reads the wrong
    (or no) schema.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", str(org_id).lower()).strip("_")
    return f"{slug}__meta_ads"


def _redirect_uri(settings: Settings) -> str:
    return f"{settings.web_base_url.rstrip('/')}/fivetran-return"


async def start_connection(settings: Settings, http: httpx.AsyncClient, org_id: UUID) -> dict:
    if not settings.fivetran_group_id:
        raise ConnectError(500, "FIVETRAN_GROUP_ID not configured")
    schema = schema_for(org_id)
    # Fivetran rejects a duplicate schema, so drop any existing connector for this org
    # (an abandoned attempt or a reconnect) before re-creating.
    existing = await fivetran_client.find_connector_by_schema(
        settings, http, settings.fivetran_group_id, schema
    )
    if existing:
        await fivetran_client.delete_connector(settings, http, existing)
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
