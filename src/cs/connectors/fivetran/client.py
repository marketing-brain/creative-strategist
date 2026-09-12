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
    "basic_ad",
    "basic_ad_actions",
    "basic_ad_action_values",
)

# Ad-level video retention has no prebuilt Fivetran report -- the prebuilt breakdown
# tables (action_video_view_type, action_video_sound) are account-level only, confirmed
# against a live connector. This custom report is the only way to get the watch-through
# curve per ad. Defined here (not just run once by hand) so every org's connector gets it
# automatically at connect-time via lock_down_and_activate, with no manual per-org step --
# required to scale past a handful of hand-configured accounts.
#
# CUSTOM_REPORTS_ENABLED is False: see the DISABLED note on lock_down_and_activate below.
# Typed as bool (not inferred as Literal[False]) so the `if` there isn't flagged dead code.
CUSTOM_REPORTS_ENABLED: bool = False
CUSTOM_REPORTS: tuple[dict, ...] = (
    {
        "table_name": "video_retention_ad",
        "config_type": "Custom",
        "level": "ad",
        "aggregation": "Day",
        "action_report_time": "mixed",
        "fields": [
            "video_p25_watched_actions",
            "video_p50_watched_actions",
            "video_p75_watched_actions",
            "video_p100_watched_actions",
            "video_thruplay_watched_actions",
            "video_play_actions",
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
            "config": {"schema": schema, "pause_after_trial": True},
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


async def _configure_custom_reports(
    settings: Settings, http: httpx.AsyncClient, connector_id: str
) -> None:
    current = await get_connector(settings, http, connector_id)
    new_config = dict(current["config"])
    by_name = {t["table_name"]: t for t in new_config.get("custom_tables", [])}
    for report in CUSTOM_REPORTS:
        by_name[report["table_name"]] = report
    new_config["custom_tables"] = list(by_name.values())
    await _call(settings, http, "PATCH", f"/connectors/{connector_id}", {"config": new_config})


async def _custom_report_table_names(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, schema: str
) -> list[str]:
    """A custom report's list<AdsActionStats> fields each unpivot into their own child
    table, named after the report with the field appended -- same pattern basic_ad's
    prebuilt basic_ad_actions/basic_ad_action_values already use. Exact child names
    aren't hardcoded here since they're only confirmed by what Fivetran actually creates;
    this discovers every table Fivetran generated for the report instead of guessing."""
    data = await _call(settings, http, "GET", f"/connectors/{connector_id}/schemas")
    tables = data.get("schemas", {}).get(schema, {}).get("tables", {})
    names = []
    for report in CUSTOM_REPORTS:
        prefix = report["table_name"]
        names += [t for t in tables if t == prefix or t.startswith(prefix + "_")]
    return names


async def lock_down_and_activate(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, schema: str
) -> None:
    # DISABLED 2026-09-12: confirmed live against two separate connectors on the same
    # org that calling _configure_custom_reports here reliably flips the connector to
    # setup_state "broken" (task: "reconnect") before it ever completes a sync -- every
    # connect attempt for that org failed the same way until this call was removed.
    # Root cause not yet understood (suspect PATCHing a full connector config mid-setup,
    # before Fivetran finishes its own OAuth handshake, invalidates something on its
    # side) -- do not re-enable without reproducing against a disposable test connector
    # first, outside of a real org's connect flow.
    if CUSTOM_REPORTS_ENABLED and CUSTOM_REPORTS:
        await _configure_custom_reports(settings, http, connector_id)
    await _call(
        settings,
        http,
        "POST",
        f"/connectors/{connector_id}/schemas/reload",
        {"exclude_mode": "EXCLUDE"},
    )
    custom_tables = await _custom_report_table_names(settings, http, connector_id, schema)
    # Right after EXCLUDE, Fivetran has barely discovered each table's real column set --
    # BLOCK_ALL would permanently lock out every column it finds later during the actual
    # sync. ALLOW_COLUMNS still blocks new tables (all we need) but lets columns self-heal.
    tables = {}
    for table in (*REQUIRED_TABLES, *custom_tables):
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
