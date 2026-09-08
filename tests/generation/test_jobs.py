from uuid import UUID

import pytest

from cs.core.queue import Job
from cs.generation import jobs, repo
from cs.generation.brand import create_brand_kit
from cs.generation.fakes import solid_png
from cs.jobs.registry import JobContext


@pytest.fixture
async def kit(db, org_id, blobs):
    await blobs.put(f"{org_id}/brand/logo.png", solid_png(200, 200, (255, 255, 255)), "image/png")
    return await create_brand_kit(
        db, org_id, ["#112233"], ["Inter"], "Warm.", ["cures"], f"{org_id}/brand/logo.png", "s"
    )


async def test_factory_failure_marks_generation_failed_instead_of_stuck(
    db, blobs, settings, org_id, kit, monkeypatch
):
    """Regression test: a provider-credential error while building the image/video/copy
    clients used to bypass run_generation's own try/except entirely (it happened while
    constructing that call's arguments, before run_generation was ever entered), leaving
    the generation row stuck at 'pending' forever with no error and a job that just died
    silently after its retries."""
    gid = await repo.create_generation(db, org_id, kit.id, "static", 2, "u1", concept="c")

    def _boom(_settings):
        raise RuntimeError("boom: missing provider credentials")

    monkeypatch.setattr(jobs, "IMAGE_FACTORY", _boom)
    ctx = JobContext(
        db=db,
        blobs=blobs,
        settings=settings,
        job=Job(UUID(int=1), org_id, "generation.run", {"generation_id": str(gid)}, 1, 5),
    )
    with pytest.raises(RuntimeError, match="boom"):
        await jobs.run(ctx)

    g = await repo.get_generation(db, org_id, gid)
    assert g["status"] == "failed"
    assert "boom" in g["error"]
