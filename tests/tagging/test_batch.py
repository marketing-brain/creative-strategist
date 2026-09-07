import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.ids import new_id
from cs.core.queue import Job
from cs.ingest import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.jobs.worker import run_once
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo as tag_repo
from cs.tagging.batch import ClaudeBatchTagger
from cs.tagging.fakes import FakeBatchTagger, FakeEmbedder
from cs.tagging.taxonomy import load_taxonomy


def _png():
    b = io.BytesIO()
    Image.new("RGB", (32, 32)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def three_images(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD",
    )
    ids = []
    for n in ("a", "b", "c"):
        cid, _ = await ingest_repo.upsert_creative(
            db,
            org_id,
            acct,
            PlatformCreative(n, "image", n, "P", None, None, "u", None, None, {}),
            "raw",
        )
        key = f"{org_id}/creatives/{cid}/original.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(
            db, org_id, cid, "original_image", key, "s", "image/png"
        )
        await ingest_repo.set_primary_asset(db, org_id, cid, aid)
        ids.append(cid)
    return ids


async def test_batch_submit_and_poll_completes_runs(
    db, blobs, settings, org_id, three_images, monkeypatch
):
    fake = FakeBatchTagger()
    monkeypatch.setattr(tagging_jobs, "BATCH_FACTORY", lambda s: fake)
    monkeypatch.setattr(tagging_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    await queue.enqueue(db, org_id, "tagging.batch_submit", {"limit": 100})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='running'") == 3
    assert await db.fetchval("SELECT status FROM tag_batch") == "submitted"
    await db.execute("UPDATE job SET run_after = now() WHERE job_type='tagging.batch_poll'")
    await run_once(db, blobs, settings, "w")  # not ended -> re-enqueued
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job WHERE job_type='tagging.batch_poll' AND status='pending'"
        )
        == 1
    )
    fake.ended.add("batch_1")
    await db.execute("UPDATE job SET run_after = now() WHERE job_type='tagging.batch_poll'")
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 3
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 3
    )


class FailingBatchTagger:
    """A batch tagger whose submit() always raises, simulating a network/API
    failure after tag_run rows for the batch have already been flipped to
    'running'."""

    async def submit(self, requests: list[dict]) -> str:
        raise RuntimeError("boom: network error")

    async def poll(self, batch_id: str) -> tuple[bool, list[dict]]:
        raise AssertionError("poll should never be reached when submit fails")


async def test_batch_submit_fails_runs_when_submit_raises(
    db, blobs, settings, org_id, three_images, monkeypatch
):
    monkeypatch.setattr(tagging_jobs, "BATCH_FACTORY", lambda s: FailingBatchTagger())
    await queue.enqueue(db, org_id, "tagging.batch_submit", {"limit": 100})
    await run_once(db, blobs, settings, "w")  # handler raises; run_once fails the job

    # No creative is left stuck at 'running' -- every run started before the
    # failed submit() call is marked 'failed' instead.
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='running'") == 0
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='failed'") == 3
    assert await db.fetchval("SELECT count(*) FROM tag_batch") == 0

    job = await db.fetchrow(
        "SELECT status, attempts FROM job WHERE job_type='tagging.batch_submit'"
    )
    assert job["status"] == "pending"  # requeued for retry, not dead yet
    assert job["attempts"] == 1

    # A failed (not running) tag_run makes the creative eligible again on the
    # next batch_submit attempt.
    taxonomy = load_taxonomy()
    tv = await tag_repo.ensure_taxonomy_version(db, taxonomy)
    ids = await tag_repo.untagged_creative_ids(db, org_id, tv, settings.claude_model_tagging, 100)
    assert set(ids) == set(three_images)


class BlobStoreFailingOnCall:
    """Wraps a real BlobStore and raises on the Nth call to get(), simulating a
    corrupt/unreadable blob for one creative partway through batch_submit's
    per-creative loop -- after start_tag_run() has already flipped that run's
    status to 'running', but before it reaches `items`."""

    def __init__(self, real, fail_at_call: int):
        self._real = real
        self._fail_at_call = fail_at_call
        self._calls = 0

    async def get(self, key: str) -> bytes:
        self._calls += 1
        if self._calls == self._fail_at_call:
            raise RuntimeError("boom: corrupt blob")
        return await self._real.get(key)

    def __getattr__(self, name):
        return getattr(self._real, name)


async def test_batch_submit_isolates_one_bad_creative(
    db, blobs, settings, org_id, three_images, monkeypatch
):
    """A single corrupt/unreadable creative partway through batch_submit's
    per-creative loop must not abort the whole batch (Global Constraint:
    per-creative failures are isolated, never abort a whole batch). Only that
    creative's tag_run is failed; the other creatives are submitted normally
    and the batch proceeds."""
    fake = FakeBatchTagger()
    monkeypatch.setattr(tagging_jobs, "BATCH_FACTORY", lambda s: fake)
    failing_blobs = BlobStoreFailingOnCall(blobs, fail_at_call=2)
    await queue.enqueue(db, org_id, "tagging.batch_submit", {"limit": 100})
    await run_once(db, failing_blobs, settings, "w")  # handler succeeds; job completes

    # The second creative's blob read raised; its tag_run is failed and the
    # loop moved on. The other two creatives were prepared and submitted
    # normally -- neither stuck at 'running' nor failed.
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='failed'") == 1
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='running'") == 2
    assert await db.fetchval("SELECT count(*) FROM tag_run") == 3

    # The batch is NOT aborted: a tag_batch row is created covering the
    # creatives that succeeded.
    batch = await db.fetchrow("SELECT status, request_count FROM tag_batch")
    assert batch is not None
    assert batch["status"] == "submitted"
    assert batch["request_count"] == 2

    job = await db.fetchrow(
        "SELECT status, attempts FROM job WHERE job_type='tagging.batch_submit'"
    )
    assert job["status"] == "done"

    # The failed creative is retryable on a future batch_submit.
    taxonomy = load_taxonomy()
    tv = await tag_repo.ensure_taxonomy_version(db, taxonomy)
    ids = await tag_repo.untagged_creative_ids(db, org_id, tv, settings.claude_model_tagging, 100)
    assert len(ids) == 1


async def test_batch_poll_noop_when_already_ended(db, blobs, settings, org_id):
    tv = await tag_repo.ensure_taxonomy_version(db, load_taxonomy())
    batch_id = new_id()
    await db.execute(
        "INSERT INTO tag_batch (id, org_id, provider_batch_id, taxonomy_version_id, model, "
        "status, request_count, ended_at) VALUES ($1,$2,$3,$4,$5,'ended',0,now())",
        batch_id,
        org_id,
        "batch_already_done",
        tv,
        settings.claude_model_tagging,
    )
    await queue.enqueue(db, org_id, "tagging.batch_poll", {"tag_batch_id": str(batch_id)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT count(*) FROM tag_run") == 0
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 0
    )


async def test_batch_poll_race_does_not_double_process(
    db, blobs, settings, org_id, three_images, monkeypatch
):
    fake = FakeBatchTagger()
    monkeypatch.setattr(tagging_jobs, "BATCH_FACTORY", lambda s: fake)
    monkeypatch.setattr(tagging_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    await queue.enqueue(db, org_id, "tagging.batch_submit", {"limit": 100})
    await run_once(db, blobs, settings, "w")
    batch_id = await db.fetchval("SELECT id FROM tag_batch")
    provider_id = await db.fetchval("SELECT provider_batch_id FROM tag_batch")
    fake.ended.add(provider_id)

    def _ctx(worker: str) -> JobContext:
        job = Job(new_id(), org_id, "tagging.batch_poll", {"tag_batch_id": str(batch_id)}, 1, 5)
        return JobContext(db=db, blobs=blobs, settings=settings, job=job)

    # Two workers both claim a batch_poll job for the same batch and race:
    # both should be able to observe status != 'ended' before either writes,
    # so this exercises the atomic UPDATE ... WHERE status<>'ended' guard
    # rather than just the cheap early-return short-circuit.
    await asyncio.gather(
        tagging_jobs.batch_poll(_ctx("w1")),
        tagging_jobs.batch_poll(_ctx("w2")),
    )

    assert await db.fetchval("SELECT status FROM tag_batch WHERE id=$1", batch_id) == "ended"
    # Exactly one of the two racing calls processed results: tag rows and
    # embed jobs are not duplicated.
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 3
    assert await db.fetchval("SELECT count(*) FROM tag") == 3 * 4  # 4 DEFAULT_TAGS per creative
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 3
    )


async def test_claude_batch_tagger_shapes_requests(settings):
    created = {}

    class Batches:
        async def create(self, requests):
            created["requests"] = requests
            return SimpleNamespace(id="msgbatch_1")

        async def retrieve(self, bid):
            return SimpleNamespace(processing_status="ended")

        async def results(self, bid):
            async def gen():
                yield SimpleNamespace(
                    custom_id="c1",
                    result=SimpleNamespace(
                        type="succeeded",
                        message=SimpleNamespace(
                            stop_reason="end_turn",
                            content=[SimpleNamespace(type="text", text='{"tags": []}')],
                            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                        ),
                    ),
                )

            return gen()

    client = SimpleNamespace(messages=SimpleNamespace(batches=Batches()))
    bt = ClaudeBatchTagger(settings, client=client)
    bid = await bt.submit(
        [
            {
                "custom_id": "c1",
                "images": [(b"\xff\xd8\xff", "image/jpeg")],
                "user_text": "t",
                "schema": {"type": "object"},
            }
        ]
    )
    assert bid == "msgbatch_1"
    params = created["requests"][0]["params"]
    assert (
        "fallbacks" not in params
        and "betas" not in params
        and params["model"] == settings.claude_model_tagging
    )
    ended, results = await bt.poll(bid)
    assert ended and results[0]["ok"] and results[0]["custom_id"] == "c1"
