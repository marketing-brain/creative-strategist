import io
from types import SimpleNamespace

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.ingest import repo as ingest_repo
from cs.jobs.worker import run_once
from cs.tagging import jobs as tagging_jobs
from cs.tagging.batch import ClaudeBatchTagger
from cs.tagging.fakes import FakeBatchTagger, FakeEmbedder


def _png():
    b = io.BytesIO()
    Image.new("RGB", (32, 32)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def three_images(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
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
