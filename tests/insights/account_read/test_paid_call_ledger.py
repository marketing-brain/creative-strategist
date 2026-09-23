"""A draft is on the books before it is requested.

On 2026-09-22 the gateway disconnected mid-request, over and over, on a call that had very
likely already generated. The SDK retried underneath, the job retried above, and nothing
recorded any of it: the only sign that money had gone was the gateway's budget running out.
"""

import pytest

from cs.core import model_calls
from cs.core.ids import new_id
from cs.insights.account_read.jobs import PURPOSE, _Ledger
from cs.insights.account_read.types import RunResult
from cs.insights.account_read.writer import MAX_ATTEMPTS, write_read


class _Dropped(Exception):
    """What the gateway does to a long request: no answer, no status, no usage."""


class AnsweringWriter:
    """Answers every time, with a draft too thin to pass the content checks."""

    async def complete(
        self, system: str, user: str, reasoning: dict | None = None
    ) -> tuple[str, int, int]:
        return '{"overview": "o", "findings": []}', 100, 50


class DisconnectingWriter:
    def __init__(self, fail_after: int = 0):
        self.calls = 0
        self.fail_after = fail_after

    async def complete(
        self, system: str, user: str, reasoning: dict | None = None
    ) -> tuple[str, int, int]:
        self.calls += 1
        if self.calls > self.fail_after:
            raise _Dropped("Server disconnected without sending a response")
        return '{"overview": "o", "findings": []}', 100, 50


def _settings(model="m"):
    class S:
        account_read_model = model

    return S()


def _run() -> RunResult:
    return RunResult(
        units=[],
        tests=[],
        overlaps=[],
        stratified=[],
        retention_by_reveal={},
        summary={"units": 0, "creatives": 0, "spend": 0, "purchases": 0},
        bin_edges={},
    )


async def test_a_call_that_never_answers_is_still_on_the_books(db, org_id, ad_account_id):
    ledger = _Ledger(db, org_id, ad_account_id, _settings())

    with pytest.raises(_Dropped):
        await write_read(_run(), DisconnectingWriter(fail_after=0), ledger)

    rows = await db.fetch("SELECT status, error FROM model_call WHERE purpose=$1", PURPOSE)
    assert [r["status"] for r in rows] == ["failed"]
    assert "disconnected" in rows[0]["error"]
    assert await model_calls.attempts_for(db, PURPOSE, ledger.key) == 1


async def test_a_second_run_counts_against_the_same_key(db, org_id, ad_account_id):
    """Re-running an account read the same day continues the count; it does not start over,
    so 'how many drafts have we paid for today' has one answer."""
    ledger = _Ledger(db, org_id, ad_account_id, _settings())
    with pytest.raises(_Dropped):
        await write_read(_run(), DisconnectingWriter(fail_after=0), ledger)
    with pytest.raises(_Dropped):
        await write_read(
            _run(),
            DisconnectingWriter(fail_after=0),
            _Ledger(db, org_id, ad_account_id, _settings()),
        )

    assert await model_calls.attempts_for(db, PURPOSE, ledger.key) == 2
    attempts = [
        r["attempt"] for r in await db.fetch("SELECT attempt FROM model_call ORDER BY attempt")
    ]
    assert attempts == [1, 2]


async def test_every_draft_the_writer_answers_is_booked_with_its_tokens(db, org_id, ad_account_id):
    """A thin draft fails the checks and the loop asks again, up to MAX_ATTEMPTS. Each of
    those is a separate paid call and gets its own row."""
    ledger = _Ledger(db, org_id, ad_account_id, _settings())
    read, problems, _, _ = await write_read(_run(), AnsweringWriter(), ledger)

    assert read is None and problems  # the draft never passed its checks
    rows = await db.fetch("SELECT status, input_tokens, output_tokens FROM model_call")
    assert len(rows) == MAX_ATTEMPTS
    assert {r["status"] for r in rows} == {"done"}
    assert {(r["input_tokens"], r["output_tokens"]) for r in rows} == {(100, 50)}


async def test_a_caller_without_a_ledger_still_works(db):
    """The fakes in other tests pass no ledger, and nothing is booked for them."""
    await write_read(_run(), AnsweringWriter())
    assert await db.fetchval("SELECT count(*) FROM model_call") == 0


async def test_attempts_for_is_zero_for_work_never_sent(db):
    assert await model_calls.attempts_for(db, PURPOSE, f"{new_id()}:2026-09-23") == 0
