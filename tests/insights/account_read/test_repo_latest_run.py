"""latest_run must never read the multi-megabyte `result` payload on a page load.
On 2026-09-18 the first form (result and read for LIMIT 20) ran for minutes per call under
concurrent page loads and pinned the database instance until it stopped answering. Reading
it for the one picked row did the same, more briefly, on 2026-09-20. A page load reads the
stored `page` slice; `result` is read once per run per process, behind a lock."""

import asyncio
from uuid import uuid4

from cs.insights.account_read import page as page_mod
from cs.insights.account_read import repo


class RecordingDb:
    """Answers the listing query with `listing` and the payload query with `payload`, and
    records every SQL string it was asked."""

    def __init__(self, listing, payload):
        self.listing, self.payload, self.sql = listing, payload, []

    async def fetch(self, sql, *args):
        self.sql.append(sql)
        return self.listing

    async def fetchrow(self, sql, *args):
        self.sql.append(sql)
        return self.payload

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        await asyncio.sleep(0)  # let the other waiting loads pile up behind the lock
        return self.result

    async def execute(self, sql, *args):
        self.sql.append(sql)


async def test_listing_query_carries_no_payload_and_the_chosen_row_is_fetched_alone():
    newest, done = uuid4(), uuid4()
    listing = [
        {"id": newest, "status": "rejected", "n_units": 56, "error": "bad", "created_at": 2},
        {"id": done, "status": "done", "n_units": 56, "error": None, "created_at": 1},
    ]
    payload = {
        "id": done,
        "status": "done",
        "n_units": 56,
        "window_start": None,
        "window_end": None,
        "page": {"v": repo.PAGE_VERSION, "units": []},
        "read": {"overview": "o"},
        "error": None,
        "created_at": 1,
    }
    db = RecordingDb(listing, payload)
    row = await repo.latest_run(db, uuid4(), uuid4())
    assert row["id"] == done and row["page"]["units"] == []
    assert row["newer_failed_error"] == "bad"
    listing_sql, payload_sql = db.sql
    assert "result" not in listing_sql and "read" not in listing_sql.replace("account_read_run", "")
    assert "WHERE id" in payload_sql and "result" not in payload_sql


async def test_a_run_without_a_page_costs_one_heavy_read_however_many_loads_arrive_together():
    run = uuid4()
    listing = [{"id": run, "status": "done", "n_units": 1, "error": None, "created_at": 1}]
    read = {"overview": "Late reveals earn the click (P2).", "findings": []}
    payload = {
        "id": run,
        "status": "done",
        "n_units": 1,
        "window_start": None,
        "window_end": None,
        "page": None,
        "read": read,
        "error": None,
        "created_at": 1,
    }
    db = RecordingDb(listing, payload)
    db.result = {
        "summary": {"units": 1},
        "units": [{"sha256": "a", "facets": ["dropped"]}],
        "tests": [
            {"dimension": "d", "value": "x", "metric": "cpa"},
            {"dimension": "d", "value": "y", "metric": "link_ctr"},
        ],
    }
    rows = await asyncio.gather(*(repo.latest_run(db, uuid4(), uuid4()) for _ in range(10)))
    assert sum("SELECT result" in s for s in db.sql) == 1
    page = rows[0]["page"]
    assert [t["pid"] for t in page["tests"]] == ["P2"] and page["units"] == [
        {k: ("a" if k == "sha256" else None) for k in page_mod.UNIT_KEYS}
    ]
    assert sum(s.startswith("UPDATE account_read_run SET page") for s in db.sql) == 1


async def test_no_rows_means_none_and_no_second_query():
    db = RecordingDb([], None)
    assert await repo.latest_run(db, uuid4(), uuid4()) is None
    assert len(db.sql) == 1
