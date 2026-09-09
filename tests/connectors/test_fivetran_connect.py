from uuid import UUID

import pytest

from cs.connectors.fivetran import connect


def test_schema_for_is_stable_and_sanitized():
    org = UUID("01234567-89ab-cdef-0123-456789abcdef")
    schema = connect.schema_for(org)
    assert schema == "org_01234567_89ab_cdef_0123_456789abcdef__meta_ads"
    assert schema == connect.schema_for(org)  # deterministic


async def test_start_connection_rejects_when_existing_connector_found(settings, monkeypatch):
    settings.fivetran_group_id = "grp_1"
    calls = []
    monkeypatch.setattr(
        connect.fivetran_client,
        "find_connector_by_schema",
        lambda s, h, g, sc: _record(calls, "find", "old_conn"),
    )
    monkeypatch.setattr(
        connect.fivetran_client,
        "delete_connector",
        lambda s, h, cid: _record(calls, "delete", None),
    )
    org = UUID(int=1)
    with pytest.raises(connect.ConnectError) as exc:
        await connect.start_connection(settings, None, org)
    assert exc.value.status == 409
    assert [c[0] for c in calls] == ["find"]  # never deletes


async def test_disconnect_connection_deletes_existing(settings, monkeypatch):
    settings.fivetran_group_id = "grp_1"
    calls = []
    monkeypatch.setattr(
        connect.fivetran_client,
        "find_connector_by_schema",
        lambda s, h, g, sc: _record(calls, "find", "old_conn"),
    )
    monkeypatch.setattr(
        connect.fivetran_client,
        "delete_connector",
        lambda s, h, cid: _record(calls, "delete", None),
    )
    await connect.disconnect_connection(settings, None, UUID(int=1))
    assert [c[0] for c in calls] == ["find", "delete"]


async def test_disconnect_connection_raises_404_when_none_exists(settings, monkeypatch):
    settings.fivetran_group_id = "grp_1"
    monkeypatch.setattr(
        connect.fivetran_client, "find_connector_by_schema", lambda s, h, g, sc: _async_result(None)
    )
    with pytest.raises(connect.ConnectError) as exc:
        await connect.disconnect_connection(settings, None, UUID(int=1))
    assert exc.value.status == 404


async def test_start_connection_requires_group_id(settings):
    settings.fivetran_group_id = ""
    with pytest.raises(connect.ConnectError) as exc:
        await connect.start_connection(settings, None, UUID(int=1))
    assert exc.value.status == 500


async def test_finalize_connection_accepts_matching_schema(settings, monkeypatch):
    org = UUID(int=1)
    monkeypatch.setattr(
        connect.fivetran_client,
        "get_connector",
        lambda s, h, cid: _async_result({"id": cid, "schema": connect.schema_for(org)}),
    )
    detail = await connect.finalize_connection(settings, None, org, "conn_1")
    assert detail["id"] == "conn_1"


async def test_finalize_connection_rejects_mismatched_schema(settings, monkeypatch):
    monkeypatch.setattr(
        connect.fivetran_client,
        "get_connector",
        lambda s, h, cid: _async_result({"id": cid, "schema": "someone_else__meta_ads"}),
    )
    with pytest.raises(connect.ConnectError) as exc:
        await connect.finalize_connection(settings, None, UUID(int=1), "conn_1")
    assert exc.value.status == 403


async def _async_result(value):
    return value


def _record(calls, name, value):
    calls.append((name, value))
    return _async_result(value)
