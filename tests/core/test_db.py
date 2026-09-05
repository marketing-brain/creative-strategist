from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from cs.core.db import _json_default
from cs.core.ids import new_id


async def test_roundtrip_and_transaction_rollback(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, $2)", oid, "acme")
    row = await db.fetchrow("SELECT name FROM org WHERE id = $1", oid)
    assert row["name"] == "acme"

    try:
        async with db.transaction() as tx:
            await tx.execute("UPDATE org SET name = 'changed' WHERE id = $1", oid)
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert await db.fetchval("SELECT name FROM org WHERE id = $1", oid) == "acme"


async def test_job_dedup_index_scoped_by_org(db):
    org_a, org_b = new_id(), new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'org a')", org_a)
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'org b')", org_b)

    for org_id in (org_a, org_b):
        await db.execute(
            "INSERT INTO job (id, org_id, job_type, dedup_key, status) "
            "VALUES ($1, $2, 'render', 'shared-key', 'pending')",
            new_id(),
            org_id,
        )


async def test_jsonb_codec_encodes_uuid_datetime_and_decimal(db):
    oid = new_id()
    await db.execute("INSERT INTO org (id, name) VALUES ($1, 'codec')", oid)
    payload = {
        "ad_account_id": new_id(),
        "since": datetime(2026, 9, 4, 12, 30, tzinfo=UTC),
        "on": date(2026, 9, 4),
        "spend": Decimal("12.34"),
    }
    jid = new_id()
    await db.execute(
        "INSERT INTO job (id, org_id, job_type, payload, status)"
        " VALUES ($1, $2, 'codec', $3, 'pending')",
        jid,
        oid,
        payload,
    )
    stored = await db.fetchval("SELECT payload FROM job WHERE id=$1", jid)
    assert stored["ad_account_id"] == str(payload["ad_account_id"])
    assert stored["since"] == "2026-09-04T12:30:00+00:00"
    assert stored["on"] == "2026-09-04"
    assert stored["spend"] == "12.34"


def test_json_default_rejects_unknown_types():
    with pytest.raises(TypeError):
        _json_default(object())
