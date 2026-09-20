import json

from cs.core import queue
from cs.insights.account_read import jobs, repo
from cs.insights.fakes import FakeAccountReadWriter
from cs.jobs.worker import run_once
from tests.insights.account_read.test_units import _creative, _tag


def _good_read_for(headlines: list[str]) -> str:
    return json.dumps(
        {
            "overview": "Creator-led ads carry the account.",
            "findings": [
                {
                    "title": "Creators earn the click",
                    "layer": "click",
                    "paragraph": "See the pattern (P1).",
                    "mechanism": "Familiar faces lower guard.",
                    "exemplars": headlines[:2],
                    "exception": "none found",
                    "why_non_obvious": "The record shows every creator ad also opens on a face.",
                    "pattern_ids": ["P1"],
                    "confidence": "suggestive",
                }
            ],
            "entangled": "none",
            "no_evidence": "nothing untested",
            "next_tests": ["Swap the creator for an expert."],
            "data_needed": ["More files."],
        }
    )


async def test_job_stores_insufficient_below_min_units(
    db, blobs, settings, org_id, ad_account_id, monkeypatch
):
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", ad_account_id)
    for i in range(3):
        await _creative(
            db,
            org_id,
            ad_account_id,
            f"c{i}",
            f"sha{i}",
            "500",
            [_tag("messenger", "single", "creator_on_camera"), _tag("duration_s", "numeric", "60")],
        )
    monkeypatch.setattr(jobs, "WRITER_FACTORY", lambda s: FakeAccountReadWriter([]))
    await queue.enqueue(
        db, org_id, "insights.compute_account_read", {"ad_account_id": str(ad_account_id)}
    )
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.latest_run(db, org_id, ad_account_id)
    assert row["status"] == "insufficient" and row["n_units"] == 3 and row["read"] is None


async def test_job_stores_done_run_with_read(
    db, blobs, settings, org_id, ad_account_id, monkeypatch
):
    await db.execute("UPDATE ad_account SET status='active' WHERE id=$1", ad_account_id)
    heads = []
    for i in range(12):
        tags = [
            _tag("messenger", "single", "creator_on_camera" if i % 2 else "expert_on_camera"),
            _tag("duration_s", "numeric", "60"),
            _tag("concept_summary", "text", f"c{i}"),
        ]
        await _creative(db, org_id, ad_account_id, f"c{i}", f"sha{i}", str(500 + i), tags)
        heads.append(f"H c{i}")
    monkeypatch.setattr(settings, "account_read_permutation_draws", 50)

    class Adaptive(FakeAccountReadWriter):  # messenger splits 6/6, so P1 always exists
        async def complete(self, system, user):
            return _good_read_for(heads), 10, 5

    monkeypatch.setattr(jobs, "WRITER_FACTORY", lambda s: Adaptive([]))
    await queue.enqueue(
        db, org_id, "insights.compute_account_read", {"ad_account_id": str(ad_account_id)}
    )
    assert await run_once(db, blobs, settings, "w1") is True
    row = await repo.latest_run(db, org_id, ad_account_id)
    assert row["status"] == "done" and row["n_units"] == 12
    # the page slice rides on the run; the full payload is fetched by id, on its own
    assert "result" not in row and row["page"]["summary"]["units"] == 12
    assert {t["pid"] for t in row["page"]["tests"]} >= set(
        row["read"]["findings"][0]["pattern_ids"]
    )
    result, read = await repo.run_result(db, row["id"]), row["read"]
    assert result["summary"]["units"] == 12 and isinstance(result["tests"], list)
    assert len(result["units"]) == 12 and result["units"][0]["facets"]
    assert read["findings"][0]["title"] == "Creators earn the click"


async def test_latest_run_and_purge(db, org_id, ad_account_id):
    a = await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="insufficient",
        n_units=1,
        result={"summary": {}},
        read=None,
        error=None,
    )
    b = await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="done",
        n_units=12,
        result={"summary": {}},
        read={"overview": "o"},
        error=None,
    )
    assert (await repo.latest_run(db, org_id, ad_account_id))["id"] == b
    await db.execute(
        "UPDATE account_read_run SET created_at = now() - interval '40 days' WHERE id=$1", a
    )
    assert await repo.purge_runs(db, days=30) == 1


async def test_latest_run_prefers_the_newest_done_run_over_a_newer_rejected_one(
    db, org_id, ad_account_id
):
    good = await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="done",
        n_units=12,
        result={"summary": {}},
        read={"overview": "good"},
        error=None,
    )
    await repo.insert_run(
        db,
        org_id,
        ad_account_id,
        status="rejected",
        n_units=12,
        result={"summary": {}},
        read=None,
        error="checks failed",
    )
    row = await repo.latest_run(db, org_id, ad_account_id)
    assert row["id"] == good and row["read"]["overview"] == "good"
    assert row["newer_failed_error"] == "checks failed"
