"""Backfill -> tag -> insight.

Ingest setup runs the real Fivetran transform against a fixture-seeded schema (no live
connector/Meta creds); tagging and insight explanation run on fakes.
"""

import io
from datetime import date, timedelta
from uuid import UUID

import httpx
import respx
from PIL import Image

from cs.connectors.fivetran.connect import schema_for
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.ingest.fivetran import insights as fivetran_insights
from cs.ingest.fivetran import transform as fivetran_transform
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema,
    insert_campaign_ad_creative,
    insert_daily_insight,
)
from cs.insights import jobs as insight_jobs
from cs.insights.fakes import FakeExplainer
from cs.jobs.registry import JobContext
from cs.jobs.worker import run_once
from cs.tagging import jobs as tag_jobs
from cs.tagging.fakes import FakeTagger
from tests.conftest import dev_headers

H = dev_headers(org="org_e2e")


async def _drain(db, blobs, settings):
    for _ in range(200):
        if not await run_once(db, blobs, settings, "w"):
            break


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(b, format="PNG")
    return b.getvalue()


@respx.mock
async def test_full_loop(client, db, blobs, settings, monkeypatch):
    # meta.download_creative (enqueued by the real transform below) does a genuine HTTP GET
    # against each fixture ad's image_url -- mock every placehold.co URL so this test stays
    # hermetic (no real network call to a third-party host) rather than flaking offline/in CI.
    respx.get(url__regex=r"^https://placehold\.co/.*").mock(
        return_value=httpx.Response(200, content=_png(), headers={"content-type": "image/png"})
    )
    q_tags = [{"dimension": "hook_type", "value": "question", "confidence": 0.9, "evidence": "e"}]
    s_tags = [{"dimension": "hook_type", "value": "statistic", "confidence": 0.9, "evidence": "e"}]
    # One shared tagger instance: TAGGER_FACTORY is invoked once per tag_creative job (once per
    # creative), so a factory that builds a fresh FakeTagger each call would reset tags_by_call
    # to its initial state every time and every creative would get the first entry (q_tags).
    tagger = FakeTagger(tags_by_call=[q_tags] * 6 + [s_tags] * 6)
    monkeypatch.setattr(tag_jobs, "TAGGER_FACTORY", lambda s: tagger)
    monkeypatch.setattr(insight_jobs, "EXPLAINER_FACTORY", lambda s: FakeExplainer())

    # 1. seed a fixture Fivetran schema (no live connector/Meta creds needed), run the
    # transform directly to discover the account (mirroring what meta.sync_from_fivetran
    # does after a real Fivetran connector's first sync completes), then activate it.
    org_id = UUID((await client.get("/me", headers=H)).json()["org_id"])
    schema = schema_for(org_id)
    await create_fixture_schema(db, schema)
    for i in range(12):
        name = f"q{i}" if i < 6 else f"s{i}"
        ctr_clicks = 30 if i < 6 else 10
        await insert_campaign_ad_creative(
            db,
            schema,
            campaign_id="c1",
            account_id="1",
            adset_id="as1",
            ad_id=f"ad_{name}",
            creative_id=name,
            name=name,
            headline=name,
            # same placehold.co URL shape scripts/seed_dev_fivetran.py uses against a real
            # connector -- mocked above so meta.download_creative's HTTP GET stays hermetic.
            image_url=f"https://placehold.co/64x64.png?text={name}",
            object_type="PHOTO",
        )
        for d in (date.today() - timedelta(days=n + 1) for n in range(10)):
            await insert_daily_insight(
                db,
                schema,
                ad_id=f"ad_{name}",
                day=d.isoformat(),
                spend=30,
                impressions=1000,
                link_clicks=ctr_clicks,
            )

    conn_id = await ingest_repo.upsert_meta_connection(db, org_id, "conn_e2e", schema)
    await db.execute("UPDATE meta_connection SET sync_status='ready' WHERE id=$1", conn_id)
    connection = await ingest_repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await fivetran_transform.discover_and_sync_entities(ctx, org_id, connection)
    await fivetran_insights.sync_insights(ctx, org_id, connection)

    acct = (await ingest_repo.list_ad_accounts(db, org_id))[0]["id"]
    await client.post(f"/accounts/{acct}/activate", headers=H)
    await _drain(db, blobs, settings)
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE status='done'") == 12

    # 2. insights
    await queue.enqueue(db, org_id, "insights.compute_patterns", {"ad_account_id": str(acct)})
    await _drain(db, blobs, settings)
    ins = (await client.get("/insights?kind=pattern", headers=H)).json()["items"]
    winner = next(i for i in ins if i["value"] == "question" and i["metric"] == "ctr")
    assert winner["lift"] > 0
