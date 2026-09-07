"""Backfill -> tag -> insight -> generate -> approve, ready to download.

All on fakes.
"""

from datetime import date, timedelta
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector
from cs.core import queue
from cs.generation import jobs as gen_jobs
from cs.generation.fakes import FakeCopyWriter, FakeImageGenerator, FakeVideoGenerator, solid_png
from cs.ingest import service as ingest_service
from cs.insights import jobs as insight_jobs
from cs.insights.fakes import FakeExplainer
from cs.jobs.worker import run_once
from cs.tagging import jobs as tag_jobs
from cs.tagging.fakes import FakeEmbedder, FakeTagger, FakeTranscriber
from tests.conftest import dev_headers

H = dev_headers(org="org_e2e")


async def _drain(db, blobs, settings):
    for _ in range(200):
        if not await run_once(db, blobs, settings, "w"):
            break


async def test_full_loop(client, db, blobs, settings, monkeypatch):
    fake = FakeConnector()
    monkeypatch.setattr(ingest_service, "CONNECTOR_FACTORY", lambda s, a, http, b: fake)
    q_tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    s_tags = [{"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e"}]
    # One shared tagger instance: TAGGER_FACTORY is invoked once per tag_creative job (once per
    # creative), so a factory that builds a fresh FakeTagger each call would reset tags_by_call
    # to its initial state every time and every creative would get the first entry (q_tags).
    tagger = FakeTagger(tags_by_call=[q_tags] * 6 + [s_tags] * 6)
    monkeypatch.setattr(tag_jobs, "TAGGER_FACTORY", lambda s: tagger)
    monkeypatch.setattr(tag_jobs, "TRANSCRIBER_FACTORY", lambda s, http: FakeTranscriber())
    monkeypatch.setattr(tag_jobs, "EMBEDDER_FACTORY", lambda s, http: FakeEmbedder())
    monkeypatch.setattr(insight_jobs, "EXPLAINER_FACTORY", lambda s: FakeExplainer())
    monkeypatch.setattr(gen_jobs, "COPYWRITER_FACTORY", lambda s: FakeCopyWriter())
    monkeypatch.setattr(gen_jobs, "IMAGE_FACTORY", lambda s: FakeImageGenerator())
    monkeypatch.setattr(gen_jobs, "VIDEO_FACTORY", lambda s: FakeVideoGenerator())

    days = [date.today() - timedelta(days=i + 1) for i in range(10)]
    for i in range(12):
        name = f"q{i}" if i < 6 else f"s{i}"
        ctr_clicks = 30 if i < 6 else 10
        fake.add_creative(
            PlatformCreative(
                name,
                "image",
                name,
                "P",
                None,
                "SHOP_NOW",
                "https://x",
                f"https://img/{name}",
                "image/png",
                {},
            ),
            solid_png(64, 64),
        )
        fake.add_ad(PlatformAd(f"ad_{name}", name, "ACTIVE", "as1", "c1", name))
        for d in days:
            fake.add_metric(
                MetricRow(
                    f"ad_{name}",
                    d,
                    "facebook:feed",
                    Decimal("30"),
                    1000,
                    ctr_clicks,
                    ctr_clicks,
                    Decimal("1"),
                    Decimal("50"),
                    0,
                    0,
                    0,
                    0,
                    {},
                )
            )

    # 1. connect + activate (backfill)
    from uuid import UUID

    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    from cs.core.crypto import encrypt_str
    from cs.ingest import repo as ingest_repo

    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "Main", "USD", encrypt_str(settings, "t"), None
    )
    await client.post(f"/accounts/{acct}/activate", headers=H)
    await _drain(db, blobs, settings)
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 12

    # 2. insights
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await _drain(db, blobs, settings)
    ins = (await client.get("/insights?kind=pattern", headers=H)).json()
    winner = next(i for i in ins if i["value"] == "question" and i["metric"] == "ctr")
    assert winner["lift"] > 0

    # 3. brand kit + generate from the insight
    await client.post(
        "/brand-kit",
        headers=H,
        json={"colors": ["#112233"], "fonts": [], "voice_rules": "", "prohibited_claims": []},
    )
    gid = (
        await client.post(
            "/generations",
            headers=H,
            json={"kind": "static", "variants": 1, "source_insight_id": winner["id"]},
        )
    ).json()["id"]
    await _drain(db, blobs, settings)
    detail = (await client.get(f"/generations/{gid}", headers=H)).json()
    assert detail["status"] == "done" and len(detail["assets"]) == 3

    # 4. approve -> asset is ready to download, with its ad copy alongside it
    aid = detail["assets"][0]["id"]
    await client.post(f"/generated-assets/{aid}/approve", headers=H)
    approved = (await client.get("/review-queue?state=approved", headers=H)).json()
    asset = next(a for a in approved if a["id"] == aid)
    assert asset["url"] and asset["headline"] and asset["primary_text"] and asset["cta_type"]
