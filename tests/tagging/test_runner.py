import io
import shutil
import subprocess
from uuid import UUID

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core.crypto import encrypt_str
from cs.core.queue import Job
from cs.ingest import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo, runner
from cs.tagging.fakes import FakeTagger, FakeTranscriber


def _png():
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 255)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def image_creative(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "u", None, None, {}),
        "raw",
    )
    key = f"{org_id}/creatives/{cid}/original.png"
    await blobs.put(key, _png(), "image/png")
    aid = await ingest_repo.insert_asset(db, org_id, cid, "original_image", key, "sha", "image/png")
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    return cid


def _ctx(db, blobs, settings, org_id, payload):
    return JobContext(
        db=db,
        blobs=blobs,
        settings=settings,
        job=Job(UUID(int=1), org_id, "tagging.tag_creative", payload, 1, 5),
    )


async def test_tag_image_creative_persists_tags(db, blobs, settings, org_id, image_creative):
    tagger, tr = FakeTagger(), FakeTranscriber()
    run_id = await runner.tag_creative(
        _ctx(db, blobs, settings, org_id, {}), image_creative, tagger, tr
    )
    assert run_id is not None and tagger.calls[0]["n_images"] == 1 and tr.calls == 0
    tags = await repo.latest_tags(db, org_id, image_creative)
    assert {t["dimension"] for t in tags} >= {"hook_type", "message_angle", "hook_summary"}
    run = await db.fetchrow("SELECT status, input_tokens, model FROM tag_run WHERE id=$1", run_id)
    assert (
        run["status"] == "done"
        and run["input_tokens"] == 100
        and run["model"] == settings.claude_model_tagging
    )
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 1
    )


async def test_tag_is_idempotent_per_taxonomy_and_model(
    db, blobs, settings, org_id, image_creative
):
    ctx = _ctx(db, blobs, settings, org_id, {})
    first = await runner.tag_creative(ctx, image_creative, FakeTagger(), FakeTranscriber())
    second = await runner.tag_creative(ctx, image_creative, FakeTagger(), FakeTranscriber())
    assert first is not None and second is None


async def test_refusal_marks_run_failed(db, blobs, settings, org_id, image_creative):
    with pytest.raises(RuntimeError):
        await runner.tag_creative(
            _ctx(db, blobs, settings, org_id, {}),
            image_creative,
            FakeTagger(refuse=True),
            FakeTranscriber(),
        )
    assert await db.fetchval("SELECT status FROM tag_run") == "failed"


async def _make_video_creative(db, blobs, settings, org_id, tmp_path):
    acct = await ingest_repo.upsert_ad_account(
        db, org_id, "1", "M", "USD", encrypt_str(settings, "t"), None
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("v1", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
    )
    p = tmp_path / "v.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(p),
        ],
        check=True,
    )
    key = f"{org_id}/creatives/{cid}/original.mp4"
    await blobs.put(key, p.read_bytes(), "video/mp4")
    aid = await ingest_repo.insert_asset(db, org_id, cid, "original_video", key, "sha", "video/mp4")
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    return cid


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_tag_video_creative_uses_keyframes_and_transcript(
    db, blobs, settings, org_id, tmp_path
):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)

    tagger, tr = FakeTagger(), FakeTranscriber("buy now")
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger, tr)
    assert (
        tagger.calls[0]["n_images"] >= 3
        and "buy now" in tagger.calls[0]["user_text"]
        and tr.calls == 1
    )
    kinds = await db.fetch(
        "SELECT kind, count(*) AS n FROM asset WHERE creative_id=$1 GROUP BY kind", cid
    )
    by = {r["kind"]: r["n"] for r in kinds}
    assert by["keyframe"] >= 3 and by["transcript"] == 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_retry_does_not_duplicate_keyframe_assets(
    db, blobs, settings, org_id, tmp_path
):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    ctx = _ctx(db, blobs, settings, org_id, {})

    with pytest.raises(RuntimeError):
        await runner.tag_creative(ctx, cid, FakeTagger(refuse=True), FakeTranscriber())
    first_count = await db.fetchval(
        "SELECT count(*) FROM asset WHERE creative_id=$1 AND kind IN ('keyframe','transcript')",
        cid,
    )
    assert first_count > 0

    await runner.tag_creative(ctx, cid, FakeTagger(), FakeTranscriber())
    second_count = await db.fetchval(
        "SELECT count(*) FROM asset WHERE creative_id=$1 AND kind IN ('keyframe','transcript')",
        cid,
    )
    assert second_count == first_count


async def test_job_handler_uses_factories(db, blobs, settings, org_id, image_creative, monkeypatch):
    monkeypatch.setattr(tagging_jobs, "TAGGER_FACTORY", lambda s: FakeTagger())
    monkeypatch.setattr(tagging_jobs, "TRANSCRIBER_FACTORY", lambda s, http: FakeTranscriber())
    from cs.jobs.worker import run_once

    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(image_creative)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT status FROM tag_run") == "done"
