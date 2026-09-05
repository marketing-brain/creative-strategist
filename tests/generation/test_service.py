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
    return await create_brand_kit(db, org_id, ["#112233"], ["Inter"], "Warm.", ["cures"], f"{org_id}/brand/logo.png", "s")


def _ctx(db, blobs, settings, org_id):
    return JobContext(db=db, blobs=blobs, settings=settings, job=Job(UUID(int=1), org_id, "generation.run", {}, 1, 5))


async def test_build_brief_from_insight(db, org_id, kit):
    d = InsightDraft("pattern", "hook_type", "question", "ctr", date(2026, 8, 1), date(2026, 8, 31), 6, Decimal("1500"), 0.02, 0.03, 0.5)
    iid = await insight_repo.insert_insight(db, org_id, d, "Questions win", "Test 2 more")
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", source_insight_id=iid)
    brief = await service.build_brief(db, org_id, await repo.get_generation(db, org_id, gid))
    assert "hook_type = question" in brief and "Questions win" in brief and "Test 2 more" in brief


async def test_run_static_generation_produces_assets(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="Morning routine")
    img = FakeImageGenerator()
    await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), img, FakeVideoGenerator())
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    assert len(assets) == 6 and {a["aspect"] for a in assets} == {"1:1", "4:5", "9:16"}
    assert all(a["review_state"] == "draft" and a["headline"] for a in assets)
    assert (await repo.get_generation(db, org_id, gid))["status"] == "done"
    assert img.calls[0]["n"] == 2 and "Morning routine" in img.calls[0]["prompt"]
    assert await blobs.exists(assets[0]["path"])


@pytest.mark.skipif(not _ffmpeg_has_drawtext(), reason="ffmpeg built without drawtext (libfreetype) support")
async def test_run_video_generation_uses_first_still(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "video", 1, "u1", concept="c")
    vid = FakeVideoGenerator()
    await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(), FakeImageGenerator(), vid)
    assets = await repo.list_generated_assets(db, org_id, generation_id=gid)
    kinds = sorted(a["kind"] for a in assets)
    assert kinds.count("video") == 1 and kinds.count("image") == 3
    assert vid.calls[0]["aspect"] == "9:16" and vid.calls[0]["still_url"].endswith(".jpg")


async def test_copy_failure_marks_generation_failed(db, blobs, settings, org_id, kit):
    gid = await repo.create_generation(db, org_id, kit.id, "static", 1, "u1", concept="c")
    with pytest.raises(RuntimeError):
        await service.run_generation(_ctx(db, blobs, settings, org_id), gid, FakeCopyWriter(sets=[]), FakeImageGenerator(), FakeVideoGenerator())
    g = await repo.get_generation(db, org_id, gid)
    assert g["status"] == "failed" and "no copy" in g["error"]
