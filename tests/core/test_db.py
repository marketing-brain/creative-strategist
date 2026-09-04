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
