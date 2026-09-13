from __future__ import annotations

import base64

import httpx

from cs.core.config import Settings

API_BASE = "https://api.fivetran.com/v1"

REQUIRED_TABLES = (
    "campaign_history",
    "ad_history",
    "creative_history",
    "ad_video_history",
    "ad_asset_feed_spec_video",
    "ad_asset_feed_spec_image",
    "ad_asset_feed_spec_asset_customization_rule",
    "ad_image_history",
    "ad_video_thumbnail",
    "basic_ad",
    "basic_ad_actions",
    "basic_ad_action_values",
)

VIDEO_RETENTION_TABLE = "video_retention_ad"

# Custom reports Fivetran builds for every new connector. Each *_actions field lands in
# its own child table named with the report's table_name as prefix.
CUSTOM_TABLES = (
    {
        "table_name": VIDEO_RETENTION_TABLE,
        "config_type": "Custom",
        "level": "ad",
        "aggregation": "Day",
        "action_report_time": "mixed",
        "fields": [
            "ad_id", "ad_name", "adset_id", "campaign_id",
            "video_p25_watched_actions", "video_p50_watched_actions",
            "video_p75_watched_actions", "video_p95_watched_actions",
            "video_p100_watched_actions", "video_thruplay_watched_actions",
            "video_avg_time_watched_actions", "video_play_actions",
        ],
    },
)


class FivetranError(RuntimeError):
    pass


def _auth_header(settings: Settings) -> str:
    raw = f"{settings.fivetran_api_key}:{settings.fivetran_api_secret}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"


async def _call(
    settings: Settings,
    http: httpx.AsyncClient,
    method: str,
    path: str,
    json_body: dict | None = None,
) -> dict:
    resp = await http.request(
        method,
        f"{API_BASE}{path}",
        json=json_body,
        headers={
            "Authorization": _auth_header(settings),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    body = (
        resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    )
    if resp.status_code >= 400:
        error_msg = body.get("message", "unknown error")
        raise FivetranError(f"Fivetran {method} {path} failed ({resp.status_code}): {error_msg}")
    return body.get("data", {})


async def create_connector(
    settings: Settings,
    http: httpx.AsyncClient,
    *,
    service: str,
    group_id: str,
    schema: str,
    redirect_uri: str,
) -> dict:
    """Create a paused connector with an embedded Connect Card.

    The create response already contains connect_card.uri (the authorize URL) -- no
    second call needed.
    """
    return await _call(
        settings,
        http,
        "POST",
        "/connectors",
        {
            "service": service,
            "group_id": group_id,
            "run_setup_tests": False,
            "paused": True,
            "config": {
                "schema": schema,
                "pause_after_trial": True,
                "custom_tables": list(CUSTOM_TABLES),
            },
            # all_fields=True is required to show the connector's own setup fields (notably
            # Facebook Ads' "sync specific accounts" picker) in the embedded Connect Card --
            # without it, Fivetran renders only the OAuth login step and silently defaults to
            # syncing every ad account the authenticating user can see. This is the only
            # account-selection control point Meta/Fivetran expose to the end user; without
            # it, a marketer managing several clients' accounts has no way to scope consent.
            "connect_card_config": {
                "redirect_uri": redirect_uri,
                "hide_setup_guide": True,
                "all_fields": True,
            },
        },
    )


async def get_connector(settings: Settings, http: httpx.AsyncClient, connector_id: str) -> dict:
    return await _call(settings, http, "GET", f"/connectors/{connector_id}")


async def set_paused(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, paused: bool
) -> None:
    await _call(settings, http, "PATCH", f"/connectors/{connector_id}", {"paused": paused})


async def get_table_columns(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, schema: str, table: str
) -> dict:
    data = await _call(
        settings,
        http,
        "GET",
        f"/connectors/{connector_id}/schemas/{schema}/tables/{table}/columns",
    )
    return data.get("columns", {})


async def lock_down_and_activate(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, schema: str
) -> None:
    reloaded = await _call(
        settings,
        http,
        "POST",
        f"/connectors/{connector_id}/schemas/reload",
        {"exclude_mode": "EXCLUDE"},
    )
    # Right after EXCLUDE, Fivetran has barely discovered each table's real column set --
    # BLOCK_ALL would permanently lock out every column it finds later during the actual
    # sync. ALLOW_COLUMNS still blocks new tables (all we need) but lets columns self-heal.
    discovered = reloaded.get("schemas", {}).get(schema, {}).get("tables", {})
    prefixes = [t["table_name"] for t in CUSTOM_TABLES]
    custom = [
        name
        for name in discovered
        if any(name == p or name.startswith(f"{p}_") for p in prefixes)
    ]
    tables = {}
    for table in (*REQUIRED_TABLES, *custom):
        columns = await get_table_columns(settings, http, connector_id, schema, table)
        tables[table] = {
            "enabled": True,
            "columns": {name: {"enabled": True} for name in columns},
        }
    await _call(
        settings,
        http,
        "PATCH",
        f"/connectors/{connector_id}/schemas",
        {
            "schemas": {schema: {"enabled": True, "tables": tables}},
            "schema_change_handling": "ALLOW_COLUMNS",
        },
    )
    await set_paused(settings, http, connector_id, False)


async def delete_connector(settings: Settings, http: httpx.AsyncClient, connector_id: str) -> None:
    await _call(settings, http, "DELETE", f"/connectors/{connector_id}")


async def find_connector_by_schema(
    settings: Settings, http: httpx.AsyncClient, group_id: str, schema: str
) -> str | None:
    """Single page (limit 1000) -- paginate if a group ever exceeds that."""
    data = await _call(settings, http, "GET", f"/groups/{group_id}/connectors?limit=1000")
    for c in data.get("items", []):
        if c.get("schema") == schema:
            return c["id"]
    return None
