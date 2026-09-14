import shutil
import subprocess
from uuid import UUID

import pytest

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.queue import Job
from cs.ingest import durations
from cs.jobs.registry import JobContext


async def _video(db, blobs, org_id, pid: str, data: bytes, duration_ms: int | None = None):
    acct = await ingest_repo.upsert_ad_account(db, org_id, "1", "Main", "USD")
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(pid, "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    key = f"{org_id}/creatives/{cid}/original.mp4"
    await blobs.put(key, data, "video/mp4")
    aid = await ingest_repo.insert_asset(
        db, org_id, cid, "original_video", key, "sha", "video/mp4", duration_ms=duration_ms
    )
    await ingest_repo.set_primary_asset(db, org_id, cid, aid)
    return cid


def _ctx(db, blobs, settings, org_id, cid) -> JobContext:
    return JobContext(
        db=db,
        blobs=blobs,
        settings=settings,
        job=Job(UUID(int=1), org_id, durations.PROBE_JOB, {"creative_id": str(cid)}, 1, 3),
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_probe_job_stores_the_video_length(db, blobs, settings, org_id, tmp_path):
    p = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
         "testsrc=size=160x120:rate=10:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         str(p)],
        check=True,
    )  # fmt: skip
    cid = await _video(db, blobs, org_id, "v", p.read_bytes())
    await durations.probe_video_duration(_ctx(db, blobs, settings, org_id, cid))
    c = await ingest_repo.get_creative(db, org_id, cid)
    assert 2_900 <= c["primary_asset_duration_ms"] <= 3_100


async def test_probe_job_leaves_a_known_length_alone(db, blobs, settings, org_id):
    cid = await _video(db, blobs, org_id, "v", b"not an mp4", duration_ms=12_000)
    await durations.probe_video_duration(_ctx(db, blobs, settings, org_id, cid))  # no probe
    assert (await ingest_repo.get_creative(db, org_id, cid))["primary_asset_duration_ms"] == 12_000


async def test_nightly_sweep_queues_unprobed_videos_and_skips_given_up_ones(
    db, blobs, settings, org_id
):
    unprobed = await _video(db, blobs, org_id, "a", b"x")
    await _video(db, blobs, org_id, "b", b"x", duration_ms=9_000)
    await durations.nightly_probe_video_durations(db, settings)
    jobs = await db.fetch(
        "SELECT payload, dedup_key FROM job WHERE job_type=$1", durations.PROBE_JOB
    )
    assert [j["payload"]["creative_id"] for j in jobs] == [str(unprobed)]
    assert jobs[0]["dedup_key"] == f"probe_duration:{unprobed}"

    await db.execute("UPDATE job SET status='dead'")
    assert await durations.unprobed_video_creatives(db, 10) == []
    await durations.nightly_probe_video_durations(db, settings)
    assert await db.fetchval("SELECT count(*) FROM job") == 1
    assert not await queue.is_dead(db, org_id, durations.PROBE_JOB, "probe_duration:other")
