import json

import httpx
import pytest
import respx

from cs.connectors.fivetran import client

F = "https://api.fivetran.com/v1"


def _settings(settings):
    settings.fivetran_api_key = "key"
    settings.fivetran_api_secret = "secret"
    return settings


@respx.mock
async def test_create_connector_returns_connect_card_url(settings):
    _settings(settings)
    respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "id": "conn_1",
                    "schema": "org1__meta_ads",
                    "connect_card": {"uri": "https://fivetran.com/connect-card/conn_1"},
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        data = await client.create_connector(
            settings,
            http,
            service="facebook_ads",
            group_id="grp_1",
            schema="org1__meta_ads",
            redirect_uri="https://app.example/fivetran-return",
        )
    assert data["id"] == "conn_1"
    assert data["connect_card"]["uri"] == "https://fivetran.com/connect-card/conn_1"
    sent = respx.calls.last.request
    assert sent.headers["Authorization"].startswith("Basic ")


@respx.mock
async def test_create_connector_requests_video_retention_custom_report(settings):
    _settings(settings)
    route = respx.post(f"{F}/connectors").mock(
        return_value=httpx.Response(200, json={"data": {"id": "conn_1"}})
    )
    async with httpx.AsyncClient() as http:
        await client.create_connector(
            settings,
            http,
            service="facebook_ads",
            group_id="grp_1",
            schema="org1__meta_ads",
            redirect_uri="https://app.example/fivetran-return",
        )
    config = json.loads(route.calls.last.request.content)["config"]
    assert [t["table_name"] for t in config["custom_tables"]] == [
        "video_retention_ad",
        "ad_signals",
        "placement_ad",
        "demographics_ad",
        "carousel_card_ad",
        "asset_video_ad",
        "asset_body_ad",
        "asset_title_ad",
    ]
    # video_retention_ad's definition is frozen: the diagnosis reads it, and changing a
    # report's definition makes Fivetran re-sync it on existing connectors.
    assert config["custom_tables"][:1] == [
        {
            "table_name": "video_retention_ad",
            "config_type": "Custom",
            "level": "ad",
            "aggregation": "Day",
            "action_report_time": "mixed",
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
        }
    ]


@respx.mock
async def test_get_connector(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "schema": "org1__meta_ads"}}
        )
    )
    async with httpx.AsyncClient() as http:
        data = await client.get_connector(settings, http, "conn_1")
    assert data["schema"] == "org1__meta_ads"


@respx.mock
async def test_delete_connector(settings):
    _settings(settings)
    route = respx.delete(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.delete_connector(settings, http, "conn_1")
    assert route.called


@respx.mock
async def test_find_connector_by_schema_matches(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "items": [
                        {"id": "conn_1", "schema": "org1__meta_ads"},
                        {"id": "conn_2", "schema": "org2__meta_ads"},
                    ]
                }
            },
        )
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found == "conn_2"


@respx.mock
async def test_find_connector_by_schema_no_match(settings):
    _settings(settings)
    respx.get(f"{F}/groups/grp_1/connectors?limit=1000").mock(
        return_value=httpx.Response(200, json={"data": {"items": []}})
    )
    async with httpx.AsyncClient() as http:
        found = await client.find_connector_by_schema(settings, http, "grp_1", "org2__meta_ads")
    assert found is None


@respx.mock
async def test_error_response_raises_fivetran_error(settings):
    _settings(settings)
    respx.get(f"{F}/connectors/bad").mock(
        return_value=httpx.Response(400, json={"message": "connector not found"})
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(client.FivetranError, match="connector not found"):
            await client.get_connector(settings, http, "bad")


@respx.mock
async def test_set_paused(settings):
    _settings(settings)
    route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.set_paused(settings, http, "conn_1", False)
    assert json.loads(route.calls.last.request.content) == {"paused": False}


@respx.mock
async def test_lock_down_and_activate_excludes_then_allows_only_required_tables(settings):
    _settings(settings)
    reload_route = respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    for table in client.REQUIRED_TABLES:
        respx.get(f"{F}/connectors/conn_1/schemas/org1__meta_ads/tables/{table}/columns").mock(
            return_value=httpx.Response(
                200,
                json={"data": {"columns": {"id": {"enabled": True}, "spend": {"enabled": False}}}},
            )
        )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    unpause_route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        await client.lock_down_and_activate(settings, http, "conn_1", "org1__meta_ads")

    reload_sent = json.loads(reload_route.calls.last.request.content)
    assert reload_sent == {"exclude_mode": "EXCLUDE"}

    schema_sent = json.loads(schema_route.calls.last.request.content)
    assert schema_sent["schema_change_handling"] == "ALLOW_COLUMNS"
    tables = schema_sent["schemas"]["org1__meta_ads"]["tables"]
    assert set(tables) == set(client.REQUIRED_TABLES)
    assert all(t["enabled"] for t in tables.values())
    assert all(set(t["columns"]) == {"id", "spend"} for t in tables.values())
    assert all(c["enabled"] for t in tables.values() for c in t["columns"].values())

    assert json.loads(unpause_route.calls.last.request.content) == {"paused": False}


@respx.mock
async def test_lock_down_keeps_custom_report_tables_the_reload_discovers(settings):
    # Fivetran splits each *_actions field of a custom report into its own child table,
    # and its exact naming (p25 vs p_25) isn't something to hardcode. Enable the report's
    # main table and every child table the reload reports under its prefix -- nothing else.
    _settings(settings)
    discovered = {
        "video_retention_ad": {"enabled": False},
        "video_retention_ad_video_p25_watched_actions": {"enabled": False},
        "video_retention_ad_video_play_actions": {"enabled": False},
        "demographics_age": {"enabled": False},
        "video_retention_adx": {"enabled": False},
    }
    respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(
            200, json={"data": {"schemas": {"org1__meta_ads": {"tables": discovered}}}}
        )
    )
    for table in (*client.REQUIRED_TABLES, *discovered):
        respx.get(f"{F}/connectors/conn_1/schemas/org1__meta_ads/tables/{table}/columns").mock(
            return_value=httpx.Response(200, json={"data": {"columns": {"id": {"enabled": True}}}})
        )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.patch(f"{F}/connectors/conn_1").mock(return_value=httpx.Response(200, json={"data": {}}))
    async with httpx.AsyncClient() as http:
        await client.lock_down_and_activate(settings, http, "conn_1", "org1__meta_ads")

    sent = json.loads(schema_route.calls.last.request.content)
    tables = sent["schemas"]["org1__meta_ads"]["tables"]
    assert set(tables) == {
        *client.REQUIRED_TABLES,
        "video_retention_ad",
        "video_retention_ad_video_p25_watched_actions",
        "video_retention_ad_video_play_actions",
    }


# Meta's allowed breakdown permutations that these reports use
# (https://developers.facebook.com/docs/marketing-api/insights/breakdowns).
_ALLOWED_BREAKDOWNS = {
    (),
    ("age", "gender"),
    ("platform_position", "publisher_platform"),
    ("video_asset",),
    ("body_asset",),
    ("title_asset",),
}
_ASSET_BREAKDOWNS = {
    "video_asset",
    "body_asset",
    "title_asset",
    "image_asset",
    "description_asset",
    "call_to_action_asset",
    "link_url_asset",
    "ad_format_asset",
}


def test_reports_follow_fivetran_and_meta_rules():
    for t in client.CUSTOM_TABLES:
        name = t["table_name"]
        # Fivetran queries at ad level only when ad_id is a field.
        assert t["level"] == "ad" and "ad_id" in t["fields"], name
        # Only the delivery-mix reports aggregate monthly; everything else is daily.
        monthly = (
            "demographics_ad",
            "carousel_card_ad",
            "asset_video_ad",
            "asset_body_ad",
            "asset_title_ad",
        )
        weekly = ("placement_ad",)
        expected = "Month" if name in monthly else "Week" if name in weekly else "Day"
        assert t["aggregation"] == expected, name
        assert len(t["fields"]) == len(set(t["fields"])), name
        breakdowns = tuple(sorted(t.get("breakdowns") or []))
        assert breakdowns in _ALLOWED_BREAKDOWNS, name
        # Fivetran's setup test fails a report with action breakdowns but no `actions`
        # field and marks the whole connector broken (org 2, 2026-09-15).
        if t.get("action_breakdowns"):
            assert "actions" in t["fields"], name
            assert not breakdowns, name
        if set(breakdowns) & _ASSET_BREAKDOWNS:
            assert len(breakdowns) == 1, name
            assert set(t["fields"]) <= client.ASSET_BREAKDOWN_FIELDS, name
        assert not any(
            b.startswith("hourly_stats") or b == "frequency_value" for b in breakdowns
        ), name
        if name not in client.FROZEN_REPORTS:
            assert t["attribution_windows"] == ["CLICK_7D", "VIEW_1D", "INCREMENTALITY"], name


def test_reports_carry_the_signals_the_insights_need():
    by_name = {t["table_name"]: t for t in client.CUSTOM_TABLES}
    signals = set(by_name["ad_signals"]["fields"])
    assert {"outbound_clicks", "video_play_curve_actions"} <= signals
    # Placement feeds the funnel's Context tier, compared against its trailing 30-day window.
    assert by_name["placement_ad"]["aggregation"] == "Week"
    # Rejected by Fivetran on a daily ad-level report (org 2, 2026-09-15).
    assert not signals & {
        "quality_ranking",
        "engagement_rate_ranking",
        "conversion_rate_ranking",
        "unique_outbound_clicks",
    }
    assert "actions" in by_name["placement_ad"]["fields"]
    assert by_name["demographics_ad"]["breakdowns"] == ["age", "gender"]
    assert "action_carousel_card_id" in by_name["carousel_card_ad"]["action_breakdowns"]


def test_plan_adds_missing_extends_ours_and_leaves_the_rest_alone():
    placement = next(t for t in client.CUSTOM_TABLES if t["table_name"] == "placement_ad")
    connector = {
        "config": {
            "custom_tables": [
                {"table_name": "video_retention_ad", "fields": ["ad_id"]},
                {"table_name": "hand_made_report", "fields": ["ad_id"]},
                {
                    "table_name": "ad_signals",
                    "fields": ["ad_id", "outbound_clicks", "video_play_curve_actions"],
                },
                {
                    "table_name": "placement_ad",
                    "breakdowns": ["platform_position", "publisher_platform"],
                    "fields": [*placement["fields"][:3], "a_field_someone_added"],
                },
                {"table_name": "demographics_ad", "breakdowns": ["age"], "fields": ["ad_id"]},
            ]
        }
    }
    plan = client.plan_custom_reports(connector)
    assert plan["missing"] == [
        "carousel_card_ad",
        "asset_video_ad",
        "asset_body_ad",
        "asset_title_ad",
    ]
    # Frozen and hand-made reports are never extended; ours only gain fields, never lose them.
    assert set(plan["extend"]) == {"ad_signals", "placement_ad"}
    assert "video_play_curve_actions" not in plan["extend"]["ad_signals"]  # already present
    assert plan["extend"]["placement_ad"] == placement["fields"][3:]
    assert plan["conflicts"] == ["demographics_ad"]
    assert client.plan_custom_reports({})["missing"] == [
        t["table_name"] for t in client.CUSTOM_TABLES
    ]


def _connector_route(echoed):
    return respx.get(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(
            200, json={"data": {"id": "conn_1", "config": {"custom_tables": echoed}}}
        )
    )


def _test_route(*states):
    # repeat the last state so an extra call does not exhaust the mock
    states = (*states, states[-1])
    return respx.post(f"{F}/connectors/conn_1/test").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": {
                        "status": {"setup_state": state},
                        "setup_tests": [
                            {
                                "title": "Validating x",
                                "status": "PASSED" if state == "connected" else "FAILED",
                                "message": "bad",
                            }
                        ],
                    }
                },
            )
            for state in states
        ]
    )


@respx.mock
async def test_apply_custom_reports_adds_extends_tests_and_enables_tables(settings):
    _settings(settings)
    frozen = {
        "table_name": "video_retention_ad",
        "fields": ["ad_id", "video_play_actions"],
        "attribution_windows": ["VIEW_1D", "CLICK_7D", "INCREMENTALITY"],
    }
    signals = {"table_name": "ad_signals", "fields": ["ad_id"]}
    hand_made = {"table_name": "hand_made_report", "fields": ["ad_id"]}
    # Shape of org 2's live connector: GET also echoes deprecated read-only keys.
    echoed = [
        {
            **frozen,
            "view_attribution_window": "DAY_1",
            "click_attribution_window": "DAY_7",
            "engaged_view_attribution_window": "NONE",
        },
        signals,
        hand_made,
    ]
    _connector_route(echoed)
    config_route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    test_route = _test_route("connected")
    discovered = {
        "basic_ad": {"enabled": True},
        "ad_set_history": {"enabled": False},
        "video_retention_ad": {"enabled": True},
        "ad_signals": {"enabled": True},
        "ad_signals_outbound_clicks": {"enabled": False},
        "placement_ad": {"enabled": False},
        "demographics_age": {"enabled": False},
    }
    respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(
            200, json={"data": {"schemas": {"org1__meta_ads": {"tables": discovered}}}}
        )
    )
    for table in discovered:
        respx.get(f"{F}/connectors/conn_1/schemas/org1__meta_ads/tables/{table}/columns").mock(
            return_value=httpx.Response(200, json={"data": {"columns": {"ad_id": {}}}})
        )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    async with httpx.AsyncClient() as http:
        result = await client.apply_custom_reports(settings, http, "conn_1", "org1__meta_ads")

    sent = json.loads(config_route.calls.last.request.content)["config"]["custom_tables"]
    assert sent[0] == frozen and sent[2] == hand_made
    ours = next(t for t in client.CUSTOM_TABLES if t["table_name"] == "ad_signals")
    assert sent[1]["fields"][:1] == signals["fields"] and set(sent[1]["fields"]) == set(
        ours["fields"]
    )
    assert [t["table_name"] for t in sent[3:]] == [
        t["table_name"] for t in client.CUSTOM_TABLES[2:]
    ]
    assert test_route.call_count >= 1
    patch = json.loads(schema_route.calls.last.request.content)
    assert set(patch["schemas"]["org1__meta_ads"]["tables"]) == {
        "ad_signals_outbound_clicks",
        "placement_ad",
    }
    assert result["added_reports"] == [t["table_name"] for t in client.CUSTOM_TABLES[2:]]
    assert set(result["extended_reports"]) == {"ad_signals"}
    assert result["enabled_tables"] == ["ad_signals_outbound_clicks", "placement_ad"]


@respx.mock
async def test_apply_custom_reports_restores_previous_reports_when_setup_breaks(settings):
    _settings(settings)
    frozen = {"table_name": "video_retention_ad", "fields": ["ad_id"]}
    _connector_route([{**frozen, "click_attribution_window": "DAY_7"}])
    config_route = respx.patch(f"{F}/connectors/conn_1").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    test_route = _test_route("broken", "connected")
    reload_route = respx.post(f"{F}/connectors/conn_1/schemas/reload")
    async with httpx.AsyncClient() as http:
        with pytest.raises(client.FivetranError, match="previous reports restored"):
            await client.apply_custom_reports(settings, http, "conn_1", "org1__meta_ads")
    assert config_route.call_count == 2
    assert json.loads(config_route.calls.last.request.content) == {
        "config": {"custom_tables": [frozen]}
    }
    assert test_route.call_count == 2
    assert not reload_route.called


@respx.mock
async def test_apply_custom_reports_is_a_noop_when_everything_is_in_place(settings):
    _settings(settings)
    _connector_route([dict(t) for t in client.CUSTOM_TABLES])
    config_route = respx.patch(f"{F}/connectors/conn_1")
    test_route = respx.post(f"{F}/connectors/conn_1/test")
    respx.post(f"{F}/connectors/conn_1/schemas/reload").mock(
        return_value=httpx.Response(
            200, json={"data": {"schemas": {"s": {"tables": {"ad_signals": {"enabled": True}}}}}}
        )
    )
    schema_route = respx.patch(f"{F}/connectors/conn_1/schemas")
    async with httpx.AsyncClient() as http:
        result = await client.apply_custom_reports(settings, http, "conn_1", "s")
    assert not config_route.called and not test_route.called and not schema_route.called
    assert result == {
        "added_reports": [],
        "extended_reports": {},
        "conflicts": [],
        "enabled_tables": [],
    }


def test_only_tables_with_readers_are_required():
    # A table that syncs with no reader is pure row cost (2026-09-15 cut: 65 tables, 620 MB).
    for table in (
        "ad_set_history",
        "ad_activity",
        "ad_study",
        "ad_asset_feed_spec_body",
        "placement_ad",
        "demographics_ad",
    ):
        assert table not in client.REQUIRED_TABLES
    # Every report we defined is now synced; context tables still wait for their readers.
    assert client.ENABLED_REPORTS == {t["table_name"] for t in client.CUSTOM_TABLES}


def test_asset_feed_spec_video_is_required():
    # Rule 4 of the classification contract reads this table. lock_down_and_activate
    # sets ALLOW_COLUMNS, which blocks tables discovered later -- so a table missing
    # from REQUIRED_TABLES can never appear for a new connection.
    assert "ad_asset_feed_spec_video" in client.REQUIRED_TABLES


def test_placement_customization_media_tables_are_required():
    # PLACEMENT asset-customization creatives keep their media in the asset feed spec,
    # not image_url: images per placement, the rule mapping assets to placements, the
    # full-size image library the hashes resolve against, and video thumbnails.
    for table in (
        "ad_asset_feed_spec_image",
        "ad_asset_feed_spec_asset_customization_rule",
        "ad_image_history",
        "ad_video_thumbnail",
    ):
        assert table in client.REQUIRED_TABLES
