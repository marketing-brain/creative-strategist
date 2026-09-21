import io
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from PIL import Image

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.queue import Job
from cs.insights import jobs as insight_jobs
from cs.insights import repo
from cs.insights.creative_insight_runner import write_creative_insight
from cs.insights.fakes import FakeInsightWriter
from cs.jobs.registry import JobContext
from cs.tagging import jobs as tagging_jobs
from cs.tagging.fakes import FakeTagger

TODAY = datetime.now(UTC).date()
CLEAN = (
    "Attention is the strong layer, which is consistent with the product close-up "
    "reading at a glance."
)


def _png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 255)).save(b, format="PNG")
    return b.getvalue()


def _ctx(db, blobs, settings, org_id) -> JobContext:
    return JobContext(
        db=db,
        blobs=blobs,
        settings=settings,
        job=Job(UUID(int=1), org_id, insight_jobs.CREATIVE_INSIGHT_JOB, {}, 1, 3),
    )


async def _account(db, org_id) -> UUID:
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    await ingest_repo.set_ad_account_status(db, org_id, acct, "active")
    return acct


async def _creative(
    db, blobs, org_id, acct, pid: str, fmt: str, *, daily_impressions: int, ctr: float,
    data: bytes | None = None,
    sha: str | None = None,  # files sharing a hash are one footage group; default: its own
) -> UUID:  # fmt: skip
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(pid, fmt, "H " + pid, "P", None, "SHOP_NOW", "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    await ingest_repo.upsert_ad(
        db, org_id, acct, PlatformAd("ad_" + pid, pid, "ACTIVE", None, None, pid), cid
    )
    if data is not None:
        ext, mime, kind = ("mp4", "video/mp4", "original_video") if fmt == "video" else (
            "png", "image/png", "original_image"
        )  # fmt: skip
        key = f"{org_id}/creatives/{cid}/original.{ext}"
        await blobs.put(key, data, mime)
        aid = await ingest_repo.insert_asset(db, org_id, cid, kind, key, sha or f"sha-{pid}", mime)
        await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    clicks = int(daily_impressions * ctr)
    video = fmt == "video"
    rows = [
        MetricRow(
            "ad_" + pid,
            TODAY - timedelta(days=i),
            "all",
            Decimal("10"),
            daily_impressions,
            clicks,
            clicks,
            Decimal(0),
            Decimal(0),
            daily_impressions // 3 if video else 0,
            daily_impressions // 10 if video else 0,
            daily_impressions // 6 if video else 0,
            daily_impressions // 12 if video else 0,
            {},
            video_plays=daily_impressions // 3 if video else 0,
            video_p25=daily_impressions // 4 if video else 0,
        )  # fmt: skip
        for i in range(10)
    ]
    await ingest_repo.upsert_metrics(db, org_id, acct, rows)
    return cid


async def _readable_image(db, blobs, org_id) -> UUID:
    acct = await _account(db, org_id)
    target = await _creative(
        db, blobs, org_id, acct, "t", "image", daily_impressions=5_000, ctr=0.02, data=_png()
    )
    await _creative(db, blobs, org_id, acct, "peer", "image", daily_impressions=5_000, ctr=0.01)
    return target


async def test_writes_one_insight_for_a_readable_creative(db, blobs, settings, org_id):
    target = await _readable_image(db, blobs, org_id)
    writer = FakeInsightWriter([CLEAN])
    ctx = _ctx(db, blobs, settings, org_id)
    assert await write_creative_insight(ctx, target, writer) == "done"
    row = await repo.get_creative_insight(db, org_id, target)
    assert row["status"] == "done" and row["paragraph"] == CLEAN
    assert row["moments"][0] == {"at": "00:00-00:03", "observed": "a face"}
    assert row["creative_profile"]["concept_and_message"] == "a product close-up"
    assert '<layer name="attention" status="strong" tier="reliable">' in row["brief"]
    assert row["window_end"] == TODAY and row["input_tokens"] == 100
    assert writer.calls[0]["n_images"] == 1 and not writer.calls[0]["has_video"]
    # Once only: a second run neither calls the model nor rewrites the row.
    assert await write_creative_insight(ctx, target, writer) == "exists"
    assert len(writer.calls) == 1


async def test_unreadable_creative_gets_no_insight(db, blobs, settings, org_id):
    acct = await _account(db, org_id)
    target = await _creative(
        db, blobs, org_id, acct, "t", "image", daily_impressions=20, ctr=0.02, data=_png()
    )
    await _creative(db, blobs, org_id, acct, "peer", "image", daily_impressions=5_000, ctr=0.01)
    writer = FakeInsightWriter()
    assert await write_creative_insight(_ctx(db, blobs, settings, org_id), target, writer) == (
        "unreadable"
    )
    assert writer.calls == []
    assert await repo.get_creative_insight(db, org_id, target) is None


async def test_ungrounded_draft_is_retried_with_the_problems_named(db, blobs, settings, org_id):
    target = await _readable_image(db, blobs, org_id)
    writer = FakeInsightWriter(["Link CTR is 9.99%, far above peers.", CLEAN])
    assert await write_creative_insight(_ctx(db, blobs, settings, org_id), target, writer) == "done"
    assert len(writer.calls) == 2
    assert "numbers not in the brief: 9.99%" in writer.calls[1]["user_text"]
    row = await repo.get_creative_insight(db, org_id, target)
    assert row["paragraph"] == CLEAN and row["input_tokens"] == 200


async def test_twice_ungrounded_draft_is_stored_rejected(db, blobs, settings, org_id):
    target = await _readable_image(db, blobs, org_id)
    writer = FakeInsightWriter(["CTR is 9.99%.", "CTR is 8.88% at 00:03."])
    ctx = _ctx(db, blobs, settings, org_id)
    assert await write_creative_insight(ctx, target, writer) == "rejected"
    row = await repo.get_creative_insight(db, org_id, target)
    assert row["status"] == "rejected"
    assert row["problems"] == [
        "numbers not in the brief: 8.88%",
        "timestamps cited for a non-video ad",
    ]
    assert await write_creative_insight(ctx, target, writer) == "exists"


async def test_refusal_is_recorded_as_failed_and_never_paid_for_again(db, blobs, settings, org_id):
    """The model call is paid: a failure is written down, not raised for the queue to retry."""
    target = await _readable_image(db, blobs, org_id)
    writer = FakeInsightWriter(refuse=True)
    ctx = _ctx(db, blobs, settings, org_id)
    assert await write_creative_insight(ctx, target, writer) == "failed"
    row = await repo.get_creative_insight(db, org_id, target)
    assert row["status"] == "failed" and "refused" in row["error"]
    assert await write_creative_insight(ctx, target, writer) == "exists"


async def test_video_insight_does_not_depend_on_video_tagging(db, blobs, settings, org_id):
    acct = await _account(db, org_id)
    target = await _creative(
        db, blobs, org_id, acct, "v", "video", daily_impressions=5_000, ctr=0.02, data=b"mp4"
    )
    await _creative(db, blobs, org_id, acct, "pv", "video", daily_impressions=5_000, ctr=0.01)
    creative = await ingest_repo.get_creative(db, org_id, target)
    await ingest_repo.set_asset_duration(db, org_id, creative["primary_asset_id"], 20_000)
    ctx = _ctx(db, blobs, settings.model_copy(update={"video_tagging_enabled": False}), org_id)
    writer = FakeInsightWriter([CLEAN])
    assert await write_creative_insight(ctx, target, writer) == "done"
    assert writer.calls[0]["has_video"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_backfills_duration_and_sends_the_file(db, blobs, settings, org_id, tmp_path):
    p = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=160x120:rate=10:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(p)],
        check=True,
    )  # fmt: skip
    acct = await _account(db, org_id)
    target = await _creative(
        db, blobs, org_id, acct, "v", "video", daily_impressions=5_000, ctr=0.02,
        data=p.read_bytes(),
    )  # fmt: skip
    await _creative(db, blobs, org_id, acct, "pv", "video", daily_impressions=5_000, ctr=0.01)
    writer = FakeInsightWriter(["Attention is strong; the opening at 00:00 shows the product."])
    ctx = _ctx(db, blobs, settings, org_id)
    assert await write_creative_insight(ctx, target, writer) == "done"
    assert writer.calls[0]["has_video"]
    duration = await db.fetchval(
        "SELECT a.duration_ms FROM creative c JOIN asset a ON a.id = c.primary_asset_id"
        " WHERE c.id=$1",
        target,
    )
    assert 3_900 <= duration <= 4_100
    row = await repo.get_creative_insight(db, org_id, target)
    assert "format: video · duration 00:04" in row["brief"]


async def test_tagging_job_chains_the_insight_job(db, blobs, settings, org_id, monkeypatch):
    target = await _readable_image(db, blobs, org_id)
    monkeypatch.setattr(tagging_jobs, "TAGGER_FACTORY", lambda s: FakeTagger())
    from cs.jobs.worker import run_once

    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(target)})
    await run_once(db, blobs, settings, "w")
    job = await db.fetchrow(
        "SELECT payload, dedup_key, max_attempts FROM job WHERE job_type=$1",
        insight_jobs.CREATIVE_INSIGHT_JOB,
    )
    assert job["payload"]["creative_id"] == str(target)
    assert job["dedup_key"] == f"creative_insight:{target}" and job["max_attempts"] == 3

    monkeypatch.setattr(
        insight_jobs, "INSIGHT_WRITER_FACTORY", lambda s: FakeInsightWriter([CLEAN])
    )
    await run_once(db, blobs, settings, "w")
    assert (await repo.get_creative_insight(db, org_id, target))["status"] == "done"


async def test_nightly_sweep_enqueues_readable_candidates_without_an_insight(
    db, blobs, settings, org_id
):
    acct = await _account(db, org_id)
    big = await _creative(
        db, blobs, org_id, acct, "big", "image", daily_impressions=5_000, ctr=0.02, data=_png()
    )
    await _creative(
        db, blobs, org_id, acct, "tiny", "image", daily_impressions=10, ctr=0.02, data=_png()
    )
    await _creative(db, blobs, org_id, acct, "nofile", "image", daily_impressions=5_000, ctr=0.02)
    done = await _creative(
        db, blobs, org_id, acct, "done", "image", daily_impressions=5_000, ctr=0.02, data=_png()
    )
    await repo.insert_creative_insight(
        db, org_id, done, status="done", paragraph="x", moments=[], problems=[], brief="b",
        window_start=TODAY, window_end=TODAY, model="fake", prompt_sha256="s",
        input_tokens=0, output_tokens=0,
    )  # fmt: skip

    await insight_jobs.nightly_creative_insights(db, settings)
    await insight_jobs.nightly_creative_insights(db, settings)  # pending dedup: no duplicate
    rows = await db.fetch(
        "SELECT payload FROM job WHERE job_type=$1", insight_jobs.CREATIVE_INSIGHT_JOB
    )
    assert [r["payload"]["creative_id"] for r in rows] == [str(big)]

    # A write-up whose paid call failed is on the ledger and is never queued again.
    await db.execute("DELETE FROM job WHERE job_type=$1", insight_jobs.CREATIVE_INSIGHT_JOB)
    await repo.insert_creative_insight(
        db, org_id, big, status="failed", paragraph="", moments=[], problems=[], brief="b",
        window_start=TODAY, window_end=TODAY, model="fake", prompt_sha256="s",
        input_tokens=0, output_tokens=0, error="boom",
    )  # fmt: skip
    await insight_jobs.nightly_creative_insights(db, settings)
    assert (
        await db.fetchval(
            "SELECT count(*) FROM job WHERE job_type=$1", insight_jobs.CREATIVE_INSIGHT_JOB
        )
        == 0
    )


async def _insert_insight(db, org_id, cid) -> None:
    await repo.insert_creative_insight(
        db, org_id, cid, status="rejected", paragraph="x", moments=[], problems=["p"], brief="b",
        window_start=TODAY, window_end=TODAY, model="fake", prompt_sha256="s",
        input_tokens=0, output_tokens=0,
    )  # fmt: skip


@pytest.mark.parametrize("fmt", ["image", "video"])
async def test_insights_stop_at_the_per_format_org_cap(db, blobs, settings, org_id, fmt):
    acct = await _account(db, org_id)
    data = b"mp4" if fmt == "video" else _png()
    for i in range(2):  # rejected drafts count toward the cap too
        cid = await _creative(
            db, blobs, org_id, acct, f"old{i}", fmt, daily_impressions=5_000, ctr=0.02, data=data
        )
        await _insert_insight(db, org_id, cid)
    target = await _creative(
        db, blobs, org_id, acct, "new", fmt, daily_impressions=5_000, ctr=0.03, data=data
    )
    cfg = settings.model_copy(
        update={"video_tagging_enabled": False, "creative_insight_limit_per_format": 2}
    )
    writer = FakeInsightWriter([CLEAN])
    ctx = _ctx(db, blobs, cfg, org_id)
    assert await write_creative_insight(ctx, target, writer) == "format_limit"
    assert writer.calls == []
    assert await repo.get_creative_insight(db, org_id, target) is None


async def test_cap_is_per_format(db, blobs, settings, org_id):
    acct = await _account(db, org_id)
    for i in range(2):
        cid = await _creative(
            db, blobs, org_id, acct, f"v{i}", "video", daily_impressions=5_000, ctr=0.02,
            data=b"mp4",
        )  # fmt: skip
        await _insert_insight(db, org_id, cid)
    target = await _readable_image(db, blobs, org_id)
    cfg = settings.model_copy(update={"creative_insight_limit_per_format": 2})
    ctx = _ctx(db, blobs, cfg, org_id)
    assert await write_creative_insight(ctx, target, FakeInsightWriter([CLEAN])) == "done"


async def _queued(db) -> list[str]:
    rows = await db.fetch(
        "SELECT payload FROM job WHERE job_type=$1 ORDER BY created_at, id",
        insight_jobs.CREATIVE_INSIGHT_JOB,
    )
    return [r["payload"]["creative_id"] for r in rows]


async def test_nightly_sweep_queues_only_what_the_cap_allows(db, blobs, settings, org_id):
    acct = await _account(db, org_id)
    images = [
        await _creative(db, blobs, org_id, acct, f"i{i}", "image",
                        daily_impressions=5_000 + 1_000 * i, ctr=0.02, data=_png())
        for i in range(4)
    ]  # fmt: skip
    video = await _creative(
        db, blobs, org_id, acct, "v", "video", daily_impressions=9_000, ctr=0.02, data=b"mp4"
    )
    cfg = settings.model_copy(
        update={"video_tagging_enabled": False, "creative_insight_limit_per_format": 2}
    )
    await insight_jobs.nightly_creative_insights(db, cfg)
    queued = set(await _queued(db))
    # Two highest-spend images (spend ties at $10/day, so impressions do not order them;
    # only the count is asserted) plus the video.
    assert str(video) in queued and len(queued - {str(video)}) == 2
    assert queued - {str(video)} <= {str(c) for c in images}

    # One image insight already written: only one more image may be queued.
    await db.execute("DELETE FROM job")
    await _insert_insight(db, org_id, images[0])
    await insight_jobs.nightly_creative_insights(db, cfg)
    queued = set(await _queued(db))
    assert str(video) in queued and len(queued - {str(video)}) == 1
    assert str(images[0]) not in queued

    # Both formats at the cap: nothing is queued.
    await db.execute("DELETE FROM job")
    await _insert_insight(db, org_id, images[1])
    await _insert_insight(db, org_id, video)
    other_video = await _creative(
        db, blobs, org_id, acct, "v2", "video", daily_impressions=9_000, ctr=0.02, data=b"mp4"
    )
    await _insert_insight(db, org_id, other_video)
    await insight_jobs.nightly_creative_insights(db, cfg)
    assert await _queued(db) == []


async def test_video_insight_waits_for_peer_video_lengths(db, blobs, settings, org_id):
    acct = await _account(db, org_id)
    target = await _creative(
        db, blobs, org_id, acct, "v", "video", daily_impressions=5_000, ctr=0.02, data=b"mp4"
    )
    creative = await ingest_repo.get_creative(db, org_id, target)
    await ingest_repo.set_asset_duration(db, org_id, creative["primary_asset_id"], 20_000)
    peer = await _creative(
        db, blobs, org_id, acct, "pv", "video", daily_impressions=5_000, ctr=0.01, data=b"mp4"
    )  # stored file, length not probed yet
    ctx = _ctx(db, blobs, settings, org_id)
    writer = FakeInsightWriter([CLEAN])
    assert await write_creative_insight(ctx, target, writer) == "lengths_pending"
    assert writer.calls == []

    # A peer whose probe job gave up no longer blocks.
    await queue.enqueue(db, org_id, "assets.probe_video_duration", {"creative_id": str(peer)},
                        dedup_key=f"probe_duration:{peer}")  # fmt: skip
    await db.execute("UPDATE job SET status='dead' WHERE job_type='assets.probe_video_duration'")
    assert await write_creative_insight(ctx, target, writer) == "done"
