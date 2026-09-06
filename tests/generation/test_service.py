import shutil
import subprocess
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest

from cs.core.queue import Job
from cs.generation import repo, service
from cs.generation.brand import create_brand_kit
from cs.generation.fakes import FakeCopyWriter, FakeImageGenerator, FakeVideoGenerator, solid_png
from cs.insights import repo as insight_repo
from cs.insights.types import InsightDraft
from cs.jobs.registry import JobContext


def _ffmpeg_has_drawtext() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-filters"], capture_output=True, text=True)
    return "drawtext" in out.stdout


@pytest.fixture
async def kit(db, org_id, blobs):
    await blobs.put(f"{org_id}/brand/logo.png", solid_png(200, 200, (255, 255, 255)), "image/png")
    return await create_brand_kit(
        db, org_id, ["#112233"], ["Inter"], "Warm.", ["cures"], f"{org_id}/brand/logo.png", "s"
    )


def _ctx(db, blobs, settings, org_id):
    return JobContext(
        db=db,
        blobs=blobs,
        settings=settings,
        job=Job(UUID(int=1), org_id, "generation.run", {}, 1, 5),
    )


async def test_build_brief_from_insight(db, org_id, kit):
    d = InsightDraft(
        "pattern",
        "hook_type",
        "question",
        "ctr",
        date(2026, 8, 1),
        date(2026, 8, 31),
        6,
        Decimal("1500"),
        0.02,
        0.03,
        0.5,
    )
    iid = await insight_repo.insert_insight(db, org_id, d, "Questions win", "Test 2 more")
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", source_insight_id=iid)
    brief = await service.build_brief(db, org_id, await repo.get_generation(db, org_id, gid))
    assert "hook_type = question" in brief and "Questions win" in brief and "Test 2 more" in brief


async def test_run_static_generation_produces_assets(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(
        db, org_id, kit.id, "static", 2, "u1", concept="Morning routine"
    )
    img = FakeImageGenerator()
    await service.run_generation(
        _ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), img, FakeVideoGenerator()
    )
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    assert len(assets) == 6 and {a["aspect"] for a in assets} == {"1:1", "4:5", "9:16"}
    assert all(a["review_state"] == "draft" and a["headline"] for a in assets)
    assert (await repo.get_generation(db, org_id, gid))["status"] == "done"
    assert img.calls[0]["n"] == 2 and "Morning routine" in img.calls[0]["prompt"]
    assert await blobs.exists(assets[0]["path"])


@pytest.mark.skipif(
    not _ffmpeg_has_drawtext(), reason="ffmpeg built without drawtext (libfreetype) support"
)
async def test_run_video_generation_uses_first_still(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "video", 1, "u1", concept="c")
    vid = FakeVideoGenerator()
    await service.run_generation(
        _ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), FakeImageGenerator(), vid
    )
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    kinds = sorted(a["kind"] for a in assets)
    assert kinds.count("video") == 1 and kinds.count("image") == 3
    assert vid.calls[0]["aspect"] == "9:16" and vid.calls[0]["still_url"].endswith(".jpg")


async def test_retry_does_not_duplicate_generated_assets(db, blobs, settings, org_id, kit):
    """Regression test: the job queue retries a failed generation.run attempt, and a naive
    re-run would insert a second batch of generated_asset rows (and pay for image generation
    twice) on top of whatever a prior attempt already wrote."""
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="c")
    ctx = _ctx(db, blobs, settings, org_id)
    await service.run_generation(
        ctx, gid, FakeCopyWriter(), FakeImageGenerator(), FakeVideoGenerator()
    )
    first = await repo.list_generated_assets(db, org_id, generation_id=gid)
    # Simulate a job-queue retry of the same generation_id.
    await service.run_generation(
        ctx, gid, FakeCopyWriter(), FakeImageGenerator(), FakeVideoGenerator()
    )
    second = await repo.list_generated_assets(db, org_id, generation_id=gid)
    assert len(first) == len(second) == 6


async def test_retry_preserves_already_reviewed_assets(db, blobs, settings, org_id, kit):
    """A retry's cleanup must not delete a generated_asset a human already approved or
    rejected: doing so would (a) silently discard that review decision, and (b) if the
    approved asset had already been published, violate publish_job's FK to generated_asset
    (asyncpg.ForeignKeyViolationError) since publish_job.generated_asset_id has no ON DELETE
    clause. Only 'draft' rows are cleared before a retry regenerates."""
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="c")
    ctx = _ctx(db, blobs, settings, org_id)
    await service.run_generation(
        ctx, gid, FakeCopyWriter(), FakeImageGenerator(), FakeVideoGenerator()
    )
    first = await repo.list_generated_assets(db, org_id, generation_id=gid)
    approved_id = first[0]["id"]
    assert await repo.set_review_state(db, org_id, approved_id, "approved", "u1")

    # Simulate a job-queue retry of the same generation_id.
    await service.run_generation(
        ctx, gid, FakeCopyWriter(), FakeImageGenerator(), FakeVideoGenerator()
    )
    second = await repo.list_generated_assets(db, org_id, generation_id=gid)
    by_id = {a["id"]: a for a in second}
    assert approved_id in by_id and by_id[approved_id]["review_state"] == "approved"
    assert len([a for a in second if a["review_state"] == "draft"]) == 6


class _OverGenerousCopyWriter:
    """A copywriter that ignores n and always returns more sets than requested, simulating a
    real Claude response returning more sets than asked for."""

    async def write(self, brief, kit, n):
        from cs.generation.copy import CopySet

        return [CopySet(f"H{i}", f"P{i}", "SHOP_NOW") for i in range(n + 3)]


async def test_run_generation_clamps_copywriter_oversupply(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="c")
    img = FakeImageGenerator()
    await service.run_generation(
        _ctx(db, blobs, settings, org_id), gid, _OverGenerousCopyWriter(), img, FakeVideoGenerator()
    )
    # The API caps variants at 8 and the row here requests 2 -- image generation must not be
    # asked for the 5 sets the (misbehaving) copywriter returned.
    assert img.calls[0]["n"] == 2
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    assert {a["variant"] for a in assets} == {1, 2}


async def test_run_generation_missing_generation_raises_and_is_not_swallowed(
    db, blobs, settings, org_id
):
    with pytest.raises(RuntimeError, match="generation not found"):
        await service.run_generation(
            _ctx(db, blobs, settings, org_id),
            UUID(int=999),
            FakeCopyWriter(),
            FakeImageGenerator(),
            FakeVideoGenerator(),
        )


async def test_run_generation_missing_brand_kit_marks_generation_failed(
    db, blobs, settings, org_id, kit, monkeypatch
):
    async def _no_kit(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "get_brand_kit", _no_kit)
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u1", concept="c")
    with pytest.raises(RuntimeError, match="brand kit not found"):
        await service.run_generation(
            _ctx(db, blobs, settings, org_id),
            gid,
            FakeCopyWriter(),
            FakeImageGenerator(),
            FakeVideoGenerator(),
        )
    g = await repo.get_generation(db, org_id, gid)
    assert g["status"] == "failed" and "brand kit not found" in g["error"]


async def test_compose_calls_run_off_the_event_loop_thread(
    db, blobs, settings, org_id, kit, monkeypatch
):
    """Regression test: fit_to_aspect/overlay_logo/video_with_end_card are synchronous
    Pillow/ffmpeg calls -- run_generation must offload them via asyncio.to_thread so they don't
    block the event loop, matching the fix already applied to tagging.runner for the same
    defect class. Compose functions are monkeypatched so this doesn't need a real ffmpeg."""
    import threading

    from cs.generation import service as service_mod

    seen_threads: list[threading.Thread] = []

    def fake_fit(raw, aspect):
        seen_threads.append(threading.current_thread())
        return raw

    def fake_overlay(img, logo, *args, **kwargs):
        seen_threads.append(threading.current_thread())
        return img

    def fake_video_end_card(*args, **kwargs):
        seen_threads.append(threading.current_thread())
        return b"fakemp4"

    monkeypatch.setattr(service_mod, "fit_to_aspect", fake_fit)
    monkeypatch.setattr(service_mod, "overlay_logo", fake_overlay)
    monkeypatch.setattr(service_mod, "video_with_end_card", fake_video_end_card)

    gid = await repo.create_generation(db, org_id, kit.id, "video", 1, "u1", concept="c")
    await service.run_generation(
        _ctx(db, blobs, settings, org_id),
        gid,
        FakeCopyWriter(),
        FakeImageGenerator(),
        FakeVideoGenerator(),
    )
    assert len(seen_threads) >= 2
    assert all(t is not threading.current_thread() for t in seen_threads)


async def test_copy_failure_marks_generation_failed(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u1", concept="c")
    with pytest.raises(RuntimeError):
        await service.run_generation(
            _ctx(db, blobs, settings, org_id),
            gid,
            FakeCopyWriter(sets=[]),
            FakeImageGenerator(),
            FakeVideoGenerator(),
        )
    g = await repo.get_generation(db, org_id, gid)
    assert g["status"] == "failed" and "no copy" in g["error"]
