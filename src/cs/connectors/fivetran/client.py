from __future__ import annotations

import base64

import httpx

from cs.core.config import Settings

API_BASE = "https://api.fivetran.com/v1"

# Only tables some code reads today. Everything else stays defined but disabled: a table
# that syncs with no reader is pure Fivetran row cost (the 2026-09-15 cut removed 65 such
# tables and 620 MB). Add a table here in the same change that adds its reader.
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
AD_SIGNALS_TABLE = "ad_signals"
PLACEMENT_TABLE = "placement_ad"
DEMOGRAPHICS_TABLE = "demographics_ad"
CAROUSEL_CARD_TABLE = "carousel_card_ad"
ASSET_TABLES = {
    "video_asset": "asset_video_ad",
    "body_asset": "asset_body_ad",
    "title_asset": "asset_title_ad",
}

# Reports whose definition must never change on an existing connector: the diagnosis reads
# them, and a changed definition makes Fivetran re-sync. Other reports of ours may only
# gain fields (see plan_custom_reports); nothing is ever removed.
FROZEN_REPORTS = frozenset({VIDEO_RETENTION_TABLE})

# Every report is at ad level: Fivetran queries at ad level only when `ad_id` is a field
# (its prebuilt breakdown reports have no ad_id and run at account level). Each list-typed
# field (*_actions, outbound_clicks, the play-curve histogram) lands in its own child table
# prefixed with the report's table_name.
#
# Rules encoded in tests/connectors/test_fivetran_client.py, from Fivetran and Meta docs:
# - action_breakdowns require the `actions` field, or Fivetran's setup test fails and marks
#   the connector broken (happened on org 2, 2026-09-15).
# - Breakdowns combine only in Meta's allowed permutations: age+gender and
#   publisher_platform+platform_position are allowed; demographics never combine with
#   placement.
# - Dynamic-creative asset breakdowns take one asset per report and only impressions,
#   clicks, spend, reach, actions and action_values.
# Not requested: frequency_value (Reservation campaigns only), impression_device and
# audience-time-zone hourly breakdowns (per-account enablement), hourly stats (no video
# fields allowed), and sound on/off (dropped 2026-09-15: one question, and `actions` under
# an action breakdown costs a row per action type).
#
# Grain follows how a report is read. Shares over a window (audience mix, which asset or
# carousel card won) are monthly. Anything compared against the funnel's trailing 30-day
# numbers is weekly, because weeks sum to ~30 days while calendar months never align.
# Anything feeding a trend over an ad's life (fatigue) stays daily.
_ATTRIBUTION = {
    # Matches the attribution video_retention_ad already reports (read from org 2 and org 3
    # on 2026-09-15), so new reports line up with it.
    "attribution_windows": ["CLICK_7D", "VIEW_1D", "INCREMENTALITY"],
    "use_unified_attribution_setting": True,
}
_REPORT = {
    "config_type": "Custom",
    "level": "ad",
    "aggregation": "Day",
    "action_report_time": "mixed",
}
_VIDEO_MILESTONES = [
    "video_play_actions",
    "video_thruplay_watched_actions",
    "video_p25_watched_actions",
    "video_p50_watched_actions",
    "video_p75_watched_actions",
    "video_p95_watched_actions",
    "video_p100_watched_actions",
    "video_avg_time_watched_actions",
]
ASSET_BREAKDOWN_FIELDS = frozenset(
    {"ad_id", "impressions", "clicks", "spend", "reach", "actions", "action_values"}
)

CUSTOM_TABLES = (
    {
        "table_name": VIDEO_RETENTION_TABLE,
        **_REPORT,
        "fields": [
            "ad_id",
            "ad_name",
            "adset_id",
            "campaign_id",
            "video_p25_watched_actions",
            "video_p50_watched_actions",
            "video_p75_watched_actions",
            "video_p95_watched_actions",
            "video_p100_watched_actions",
            "video_thruplay_watched_actions",
            "video_avg_time_watched_actions",
            "video_play_actions",
        ],
    },
    # Outbound clicks (real exits to the site; basic_ad has inline_link_clicks only) and the
    # per-second retention curve. Daily: fatigue compares an ad's last 7 days with its first
    # 14, and Meta returns the curve's 22 buckets as one array in one row per ad-day
    # (13,779 rows for org 3's whole history), so daily costs almost nothing.
    # Fivetran rejects quality_ranking, engagement_rate_ranking, conversion_rate_ranking and
    # unique_outbound_clicks on a daily ad-level report (PATCH 400 on org 2, 2026-09-15),
    # although its metadata lists them.
    {
        "table_name": AD_SIGNALS_TABLE,
        **_REPORT,
        **_ATTRIBUTION,
        "fields": [
            "ad_id",
            "outbound_clicks",
            "video_play_curve_actions",
            "video_play_retention_0_to_15s_actions",
            "video_play_retention_20_to_60s_actions",
            "video_play_retention_graph_actions",
        ],
    },
    # Placement (Reels / Feed / Stories ...): delivery, clicks, retention, 3-second views
    # and funnel actions per placement.
    {
        "table_name": PLACEMENT_TABLE,
        **_REPORT,
        "aggregation": "Week",
        **_ATTRIBUTION,
        "breakdowns": ["publisher_platform", "platform_position"],
        "fields": [
            "ad_id",
            "impressions",
            "spend",
            "inline_link_clicks",
            "outbound_clicks",
            "video_play_actions",
            "video_thruplay_watched_actions",
            "video_p25_watched_actions",
            "video_p50_watched_actions",
            "video_p75_watched_actions",
            "video_p100_watched_actions",
            "reach",
            "actions",
            "action_values",
        ],
    },
    # Age x gender delivery mix per ad. Meta omits off-Meta conversions under demographic
    # breakdowns (iOS 14); on-Meta actions, clicks and video stay available.
    {
        "table_name": DEMOGRAPHICS_TABLE,
        **_REPORT,
        "aggregation": "Month",
        **_ATTRIBUTION,
        "breakdowns": ["age", "gender"],
        "fields": [
            "ad_id",
            "impressions",
            "reach",
            "spend",
            "inline_link_clicks",
            "outbound_clicks",
            "video_play_actions",
            "video_thruplay_watched_actions",
            "video_p25_watched_actions",
            "video_p50_watched_actions",
            "video_p75_watched_actions",
            "video_p100_watched_actions",
            "actions",
        ],
    },
    # Per-card actions for carousels.
    {
        "table_name": CAROUSEL_CARD_TABLE,
        **_REPORT,
        "aggregation": "Month",
        **_ATTRIBUTION,
        "action_breakdowns": ["action_carousel_card_id", "action_carousel_card_name"],
        "fields": ["ad_id", "actions", "action_values", "outbound_clicks", "inline_link_clicks"],
    },
    # Flexible (dynamic-asset) ads: results per video, body text and headline variant.
    *(
        {
            "table_name": table,
            **_REPORT,
            **_ATTRIBUTION,
            "aggregation": "Month",
            "breakdowns": [breakdown],
            "fields": [
                "ad_id",
                "impressions",
                "clicks",
                "spend",
                "reach",
                "actions",
                "action_values",
            ],
        }
        for breakdown, table in ASSET_TABLES.items()
    ),
)


# Reports whose tables are actually synced. The rest stay defined on the connector (so the
# definition is ready) with their tables disabled, until a reader for them ships.
ENABLED_REPORTS = frozenset(t["table_name"] for t in CUSTOM_TABLES)


def _is_report_table(name: str) -> bool:
    return any(name == t or name.startswith(f"{t}_") for t in ENABLED_REPORTS)


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
    custom = [name for name in discovered if _is_report_table(name)]
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


ENABLE_BATCH_SIZE = 8

_READ_ONLY_REPORT_KEYS = frozenset(
    {"click_attribution_window", "view_attribution_window", "engaged_view_attribution_window"}
)


def _without_read_only(report: dict) -> dict:
    return {k: v for k, v in report.items() if k not in _READ_ONLY_REPORT_KEYS}


def plan_custom_reports(connector: dict) -> dict:
    """What applying CUSTOM_TABLES would change on an existing connector. Read-only.

    Missing reports are added. A report of ours that is already there but lacks fields gains
    them, keeping every field it has; FROZEN_REPORTS and reports someone made by hand are
    never touched. A report of ours whose breakdowns differ is reported as a conflict and
    left alone: changing breakdowns changes the grain of data already synced."""
    existing = (connector.get("config") or {}).get("custom_tables") or []
    by_name = {t.get("table_name"): t for t in existing}
    missing, extend, conflicts = [], {}, []
    for ours in CUSTOM_TABLES:
        name = ours["table_name"]
        have = by_name.get(name)
        if have is None:
            missing.append(name)
            continue
        if name in FROZEN_REPORTS:
            continue
        same_grain = sorted(have.get("breakdowns") or []) == sorted(
            ours.get("breakdowns") or []
        ) and sorted(have.get("action_breakdowns") or []) == sorted(
            ours.get("action_breakdowns") or []
        )
        if not same_grain:
            conflicts.append(name)
            continue
        new_fields = [f for f in ours["fields"] if f not in (have.get("fields") or [])]
        if new_fields:
            extend[name] = new_fields
    return {
        "present": sorted(n for n in by_name if n),
        "missing": missing,
        "extend": extend,
        "conflicts": conflicts,
    }


async def run_setup_tests(settings: Settings, http: httpx.AsyncClient, connector_id: str) -> dict:
    data = await _call(
        settings,
        http,
        "POST",
        f"/connectors/{connector_id}/test",
        {"trust_certificates": True, "trust_fingerprints": True},
    )
    return {
        "setup_state": (data.get("status") or {}).get("setup_state"),
        "failed": [
            f"{t.get('title')}: {t.get('message')}"
            for t in data.get("setup_tests") or []
            if t.get("status") not in ("PASSED", "SKIPPED")
        ],
    }


async def apply_custom_reports(
    settings: Settings, http: httpx.AsyncClient, connector_id: str, schema: str
) -> dict:
    """Add missing CUSTOM_TABLES, extend ours with missing fields, and enable their tables
    and any disabled REQUIRED_TABLES on an existing connector.

    Every config change is followed by Fivetran's setup tests. If the connector is not
    `connected` afterwards, the previous report list is restored, the tests are re-run, and
    FivetranError is raised with the failing tests: one invalid report breaks syncing for
    the whole org (org 2, 2026-09-15).

    lock_down_and_activate set schema_change_handling=ALLOW_COLUMNS, which blocks tables
    Fivetran discovers later, so tables are enabled here. Fivetran may only list a report's
    child tables after it has synced once; re-run after the next sync. Idempotent."""
    connector = await get_connector(settings, http, connector_id)
    plan = plan_custom_reports(connector)
    current = [
        _without_read_only(t) for t in (connector.get("config") or {}).get("custom_tables") or []
    ]
    if plan["missing"] or plan["extend"]:
        updated = [
            {**t, "fields": [*(t.get("fields") or []), *plan["extend"][t["table_name"]]]}
            if t.get("table_name") in plan["extend"]
            else t
            for t in current
        ]
        additions = [dict(t) for t in CUSTOM_TABLES if t["table_name"] in plan["missing"]]
        await _call(
            settings,
            http,
            "PATCH",
            f"/connectors/{connector_id}",
            {"config": {"custom_tables": [*updated, *additions]}},
        )
        tests = await run_setup_tests(settings, http, connector_id)
        if tests["setup_state"] != "connected":
            await _call(
                settings,
                http,
                "PATCH",
                f"/connectors/{connector_id}",
                {"config": {"custom_tables": current}},
            )
            restored = await run_setup_tests(settings, http, connector_id)
            raise FivetranError(
                "setup tests failed after changing custom reports; previous reports restored "
                f"(setup_state now {restored['setup_state']}): {'; '.join(tests['failed'])}"
            )
    reloaded = await _call(
        settings,
        http,
        "POST",
        f"/connectors/{connector_id}/schemas/reload",
        {"exclude_mode": "PRESERVE"},
    )
    discovered = reloaded.get("schemas", {}).get(schema, {}).get("tables", {})
    required = set(REQUIRED_TABLES)
    to_enable = sorted(
        name
        for name, t in discovered.items()
        if (_is_report_table(name) or name in required) and not t.get("enabled")
    )
    # Small batches: enabling 60 tables in one PATCH returned a Fivetran 500 (org 2,
    # 2026-09-15). Each batch is independent, so a re-run picks up whatever is still off.
    for start in range(0, len(to_enable), ENABLE_BATCH_SIZE):
        tables = {}
        for table in to_enable[start : start + ENABLE_BATCH_SIZE]:
            try:
                columns = await get_table_columns(settings, http, connector_id, schema, table)
            except FivetranError:
                # A table Fivetran has never synced reports no columns (org 2, which has no
                # insights data at all). Enable the table; its columns follow on first sync.
                tables[table] = {"enabled": True}
                continue
            tables[table] = {
                "enabled": True,
                "columns": {name: {"enabled": True} for name in columns},
            }
        await _call(
            settings,
            http,
            "PATCH",
            f"/connectors/{connector_id}/schemas",
            {"schemas": {schema: {"enabled": True, "tables": tables}}},
        )
    return {
        "added_reports": plan["missing"],
        "extended_reports": plan["extend"],
        "conflicts": plan["conflicts"],
        "enabled_tables": to_enable,
    }


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
