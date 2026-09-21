"""Storage round-trips against a real database: the plan payload and the ledger's identity."""

from cs.insights.next_plan import repo


async def test_latest_run_prefers_a_done_plan_over_a_newer_failed_one(db, org_id, ad_account_id):
    await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="done",
        plan={"make": [], "n_units": 7},
        account_read_run_id=None,
    )
    await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="failed",
        plan={"reason": "plan failed"},
        account_read_run_id=None,
        error="boom",
    )
    got = await repo.latest_run(db, org_id, ad_account_id)
    assert got is not None
    assert got["status"] == "done"
    assert got["plan"]["n_units"] == 7


async def test_no_plan_yet_reads_as_none(db, org_id, ad_account_id):
    assert await repo.latest_run(db, org_id, ad_account_id) is None


async def test_an_item_keeps_its_identity_across_runs(db, org_id, ad_account_id):
    """The ledger's whole point: a brief re-emitted tomorrow is the same recommendation, so it
    keeps its row and its first_seen_at rather than looking new every night."""
    spec = {"attributes": [["angles", "savings"]], "title": "savings"}
    await repo.upsert_items(db, org_id, ad_account_id, [("make:abc", "make", spec)])
    first = await db.fetchrow("SELECT * FROM next_item WHERE key = 'make:abc'")

    newer = {**spec, "title": "savings, restated"}
    await repo.upsert_items(db, org_id, ad_account_id, [("make:abc", "make", newer)])
    rows = await db.fetch("SELECT * FROM next_item WHERE key = 'make:abc'")

    assert len(rows) == 1
    assert rows[0]["id"] == first["id"]
    assert rows[0]["first_seen_at"] == first["first_seen_at"]
    assert rows[0]["last_seen_at"] >= first["last_seen_at"]
    assert rows[0]["spec"]["title"] == "savings, restated"
    assert rows[0]["status"] == "open"


async def test_purge_drops_only_old_runs(db, org_id, ad_account_id):
    rid = await repo.insert_run(
        db, org_id, ad_account_id, status="done", plan={}, account_read_run_id=None
    )
    assert await repo.purge_runs(db, 30) == 0
    await db.execute(
        "UPDATE next_plan_run SET created_at = now() - interval '40 days' WHERE id = $1", rid
    )
    assert await repo.purge_runs(db, 30) == 1
