import httpx
import pytest
import respx

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo
from cs.tagging.embed import GatewayEmbedder, tag_text
from cs.tagging.fakes import FakeEmbedder
from cs.tagging.taxonomy import load_taxonomy


def test_tag_text_is_deterministic():
    tags = [{"dimension": "hook_type", "value": "question"}, {"dimension": "tone", "value": "warm"}]
    a = tag_text({"headline": "H", "primary_text": "P"}, tags)
    assert a == tag_text({"headline": "H", "primary_text": "P"}, list(reversed(tags)))
    assert "hook_type=question" in a and "H" in a


@respx.mock
async def test_gateway_embedder(settings):
    settings.ai_gateway_api_key = "k"
    respx.post("https://ai-gateway.vercel.sh/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1] * 1536}]})
    )
    async with httpx.AsyncClient() as http:
        vecs = await GatewayEmbedder(settings, http).embed(["x"])
    assert len(vecs[0]) == 1536


@pytest.fixture
async def two_tagged(db, org_id, settings):
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    tv = await repo.ensure_taxonomy_version(db, load_taxonomy())
    ids = []
    for n, hook in (("c1", "question"), ("c2", "question"), ("c3", "statistic")):
        cid, _ = await ingest_repo.upsert_creative(
            db,
            org_id,
            acct,
            PlatformCreative(n, "image", hook, "P", None, None, "u", None, None, {}),
            "raw",
        )
        rid = await repo.start_tag_run(db, org_id, cid, tv, "fake")
        await repo.finish_tag_run(
            db,
            org_id,
            rid,
            [{"dimension": "hook_type", "value": hook, "confidence": 1, "evidence": ""}],
            "sha",
            1,
            1,
        )
        ids.append(cid)
    return ids


async def test_embed_job_and_similarity(db, blobs, settings, org_id, two_tagged, monkeypatch):
    monkeypatch.setattr(tagging_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    from cs.jobs.worker import run_once

    for cid in two_tagged:
        await queue.enqueue(db, org_id, "tagging.embed_creative", {"creative_id": str(cid)})
    while await run_once(db, blobs, settings, "w"):
        pass
    assert await db.fetchval("SELECT count(*) FROM creative_embedding") == 3
    sim = await repo.similar_creatives(db, org_id, two_tagged[0], k=2)
    assert sim[0]["id"] == two_tagged[1]
