"""latest_run must never pull the multi-megabyte `result` payload for every candidate row.
On 2026-09-18 the previous form (result and read for LIMIT 20) ran for minutes per call under
concurrent page loads and pinned the database instance until it stopped answering."""

from uuid import uuid4

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
        "result": {"units": []},
        "read": {"overview": "o"},
        "error": None,
        "created_at": 1,
    }
    db = RecordingDb(listing, payload)
    row = await repo.latest_run(db, uuid4(), uuid4())
    assert row["id"] == done and row["result"] == {"units": []}
    assert row["newer_failed_error"] == "bad"
    listing_sql, payload_sql = db.sql
    assert "result" not in listing_sql and "read" not in listing_sql.replace("account_read_run", "")
    assert "WHERE id" in payload_sql and "result" in payload_sql


async def test_no_rows_means_none_and_no_second_query():
    db = RecordingDb([], None)
    assert await repo.latest_run(db, uuid4(), uuid4()) is None
    assert len(db.sql) == 1
