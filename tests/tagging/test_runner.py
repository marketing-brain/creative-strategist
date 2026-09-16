import io
import shutil
import subprocess
from uuid import UUID

import pytest
from PIL import Image

from cs.connectors.base import PlatformCreative
from cs.core import queue
from cs.core import repo as ingest_repo
from cs.core.queue import Job
from cs.jobs.registry import JobContext
from cs.tagging import jobs as tagging_jobs
from cs.tagging import repo, runner, vocab
from cs.tagging.fakes import DEFAULT_V2_PAYLOAD, FakeTagger
from cs.tagging.prompts import SYSTEM_V2
from cs.tagging.taxonomy import load_taxonomy


def _png():
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (0, 0, 255)).save(b, format="PNG")
    return b.getvalue()


@pytest.fixture
async def image_creative(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("c1", "image", "H", "P", None, "SHOP_NOW", "u", None, None, {}),
        "raw",
        format_source="manual_review",
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
    tagger = FakeTagger()
    run_id = await runner.tag_creative(
        _ctx(db, blobs, settings, org_id, {}), image_creative, tagger
    )
    assert run_id is not None and tagger.calls[0]["n_images"] == 1
    tags = await repo.latest_tags(db, org_id, image_creative)
    assert {t["dimension"] for t in tags} >= {"hook_type", "message_angle", "hook_summary"}
    run = await db.fetchrow("SELECT status, input_tokens, model FROM tag_run WHERE id=$1", run_id)
    assert (
        run["status"] == "done"
        and run["input_tokens"] == 100
        and run["model"] == settings.tagging_model
    )
    # Similarity is disabled: tagging no longer chains an embed job.
    assert (
        await db.fetchval("SELECT count(*) FROM job WHERE job_type='tagging.embed_creative'") == 0
    )


async def test_tag_is_idempotent_per_taxonomy_and_model(
    db, blobs, settings, org_id, image_creative
):
    ctx = _ctx(db, blobs, settings, org_id, {})
    first = await runner.tag_creative(ctx, image_creative, FakeTagger())
    second = await runner.tag_creative(ctx, image_creative, FakeTagger())
    assert first is not None and second is None


async def test_refusal_marks_run_failed(db, blobs, settings, org_id, image_creative):
    with pytest.raises(RuntimeError):
        await runner.tag_creative(
            _ctx(db, blobs, settings, org_id, {}),
            image_creative,
            FakeTagger(refuse=True),
        )
    assert await db.fetchval("SELECT status FROM tag_run") == "failed"


async def _make_video_creative(db, blobs, settings, org_id, tmp_path):
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative(f"v1-{tmp_path.name}", "video", "H", "P", None, None, "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    tmp_path.mkdir(parents=True, exist_ok=True)
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
async def test_tag_video_creative_passes_raw_video_to_tagger(db, blobs, settings, org_id, tmp_path):
    """Video is handed to the tagger as-is (frames + audio processed natively by the
    model in one call) -- no keyframe extraction, no separate transcription."""
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)

    tagger = FakeTagger()
    run_id = await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger)
    assert run_id is not None
    assert tagger.calls[0]["n_images"] == 0
    assert tagger.calls[0]["has_video"] is True
    run = await db.fetchrow("SELECT model FROM tag_run WHERE id=$1", run_id)
    assert run["model"] == settings.video_tagging_model


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_tagging_cleans_up_assets_from_before_the_video_model_switch(
    db, blobs, settings, org_id, tmp_path
):
    """A creative tagged before the switch to native video input may still carry
    keyframe/transcript asset rows from the old extraction-based approach -- those are
    stale now (nothing produces or reads them any more) and must be cleaned up on the
    next tag run rather than left as orphaned rows referencing S3 keys."""
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    await ingest_repo.insert_asset(
        db, org_id, cid, "keyframe", "stale/keyframe/key.jpg", "sha", "image/jpeg", seq=0
    )
    await ingest_repo.insert_asset(
        db, org_id, cid, "transcript", "stale/transcript.txt", "sha", "text/plain"
    )

    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, FakeTagger())

    remaining = await db.fetchval(
        "SELECT count(*) FROM asset WHERE creative_id=$1 AND kind IN ('keyframe','transcript')",
        cid,
    )
    assert remaining == 0


async def test_job_handler_uses_factories(db, blobs, settings, org_id, image_creative, monkeypatch):
    monkeypatch.setattr(tagging_jobs, "TAGGER_FACTORY", lambda s: FakeTagger())
    from cs.jobs.worker import run_once

    await queue.enqueue(db, org_id, "tagging.tag_creative", {"creative_id": str(image_creative)})
    await run_once(db, blobs, settings, "w")
    assert await db.fetchval("SELECT status FROM tag_run") == "done"


@pytest.fixture
async def carousel_creative(db, org_id, settings, blobs):
    acct = await ingest_repo.upsert_ad_account(
        db,
        org_id,
        "1",
        "M",
        "USD",
    )
    cid, _ = await ingest_repo.upsert_creative(
        db,
        org_id,
        acct,
        PlatformCreative("cx", "carousel", "H", "P", None, "SHOP_NOW", "u", None, None, {}),
        "raw",
        format_source="manual_review",
    )
    asset_ids = []
    for i in range(3):
        key = f"{org_id}/creatives/{cid}/card_{i:02d}.png"
        await blobs.put(key, _png(), "image/png")
        aid = await ingest_repo.insert_asset(
            db, org_id, cid, "original_image", key, "sha", "image/png", seq=i
        )
        asset_ids.append(aid)
    await ingest_repo.set_primary_asset(db, org_id, cid, asset_ids[0])
    return cid


async def test_tag_carousel_creative_uses_all_cards(db, blobs, settings, org_id, carousel_creative):
    tagger = FakeTagger()
    run_id = await runner.tag_creative(
        _ctx(db, blobs, settings, org_id, {}), carousel_creative, tagger
    )
    assert run_id is not None and tagger.calls[0]["n_images"] == 3


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_tag_video_creative_writes_the_v2_profile(db, blobs, settings, org_id, tmp_path):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    tagger = FakeTagger()
    run_id = await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger)
    assert run_id is not None
    call = tagger.calls[0]
    assert call["has_video"] and call["n_images"] == 0 and call["system"] == SYSTEM_V2
    assert "single" in call["schema"]["properties"]

    rows = [dict(r) for r in await repo.latest_tags(db, org_id, cid)]
    by_dim: dict[str, list[dict]] = {}
    for r in rows:
        by_dim.setdefault(r["dimension"], []).append(r)

    # layer 0 computed in code, never from the model
    assert 3.5 <= float(by_dim["duration_s"][0]["value"]) <= 4.5
    assert by_dim["duration_s"][0]["kind"] == "numeric"
    assert by_dim["aspect_ratio"][0]["value"] == "other"  # 320x240 is 4:3
    assert by_dim["aspect_ratio"][0]["evidence"] == "computed"
    assert float(by_dim["cuts_per_10s"][0]["value"]) >= 0.0
    # single / multi / ordered
    assert by_dim["messenger"][0]["value"] == "expert_on_camera"
    assert [r["value"] for r in by_dim["hook_devices"]] == ["warning_or_risk", "audience_callout"]
    beats = [(r["value"], r["ordinal"]) for r in by_dim["beats"]]
    assert beats[:2] == [("hook", 0), ("problem", 1)]
    # open: normalized, canonicalized, counted
    concept = {r["value"]: r["raw_value"] for r in by_dim["concept_tags"]}
    assert concept == {
        "pharmacist_warning": "Pharmacist Warning",
        "otc_sleep_aid_risks": "otc_sleep_aid_risks",
    }
    assert await vocab.hints(db, org_id, "concept_tags", 10) == [
        ("otc_sleep_aid_risks", 1),
        ("pharmacist_warning", 1),
    ]
    assert by_dim["visual_motifs"][0]["value"] == "lab_report_screenshot"
    # text
    assert by_dim["transcript"][0]["value"].startswith("As a pharmacist")
    run = await db.fetchrow("SELECT status, model, input_tokens FROM tag_run WHERE id=$1", run_id)
    assert run["status"] == "done" and run["model"] == settings.video_tagging_model
    assert run["input_tokens"] == 100


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_second_video_sees_vocabulary_hints_and_reuses_canonical(
    db, blobs, settings, org_id, tmp_path
):
    first = await _make_video_creative(db, blobs, settings, org_id, tmp_path / "a")
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), first, FakeTagger())
    # merge one slug by hand, as an operator would (spec §4)
    await db.execute(
        "UPDATE tag_vocab SET canonical_slug='pharmacist_warning' "
        "WHERE org_id=$1 AND dimension='concept_tags' AND slug='otc_sleep_aid_risks'",
        org_id,
    )
    second = await _make_video_creative(db, blobs, settings, org_id, tmp_path / "b")
    tagger = FakeTagger()
    await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), second, tagger)
    text = tagger.calls[0]["user_text"]
    assert (
        "Existing slugs (count): pharmacist_warning (1)"
        in text.split("- concept_tags")[1].split("- topics")[0]
    )
    rows = await repo.latest_tags(db, org_id, second)
    concept = [(r["value"], r["raw_value"]) for r in rows if r["dimension"] == "concept_tags"]
    assert concept == [("pharmacist_warning", "Pharmacist Warning")]
    assert await vocab.hints(db, org_id, "concept_tags", 10) == [("pharmacist_warning", 2)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_profile_rejects_timestamps_beyond_the_video_length(
    db, blobs, settings, org_id, tmp_path
):
    """The 4-second fixture cannot have a beat at 47s; the model wrote minutes.seconds or
    hallucinated. The run fails rather than storing an impossible timeline."""
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    bad = {
        **DEFAULT_V2_PAYLOAD,
        "ordered": {
            "beats": [{"value": "hook", "t_start": "0:00"}, {"value": "cta", "t_start": "0:47"}]
        },
    }
    tagger = FakeTagger(payloads_by_call=[bad])
    with pytest.raises(ValueError, match="beyond the video length"):
        await runner.tag_creative(_ctx(db, blobs, settings, org_id, {}), cid, tagger)
    assert "Video length: 4." in tagger.calls[0]["user_text"]
    assert await db.fetchval("SELECT status FROM tag_run") == "failed"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_video_profile_rule_break_fails_the_run(db, blobs, settings, org_id, tmp_path):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    bad = {**DEFAULT_V2_PAYLOAD, "ordered": {"beats": []}}
    with pytest.raises(ValueError, match="beats"):
        await runner.tag_creative(
            _ctx(db, blobs, settings, org_id, {}), cid, FakeTagger(payloads_by_call=[bad])
        )
    run = await db.fetchrow("SELECT status, error FROM tag_run")
    assert run["status"] == "failed" and "beats" in run["error"]
    assert await db.fetchval("SELECT count(*) FROM tag_vocab") == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_profile_video_writes_nothing_and_leaves_open_slugs_uncanonicalized(
    db, blobs, settings, org_id, tmp_path
):
    cid = await _make_video_creative(db, blobs, settings, org_id, tmp_path)
    creative = await ingest_repo.get_creative(db, org_id, cid)
    taxonomy = load_taxonomy()
    rows, result, text, schema = await runner.profile_video(
        _ctx(db, blobs, settings, org_id, {}), creative, taxonomy, FakeTagger(), {}
    )
    assert result.input_tokens == 100 and "single" in schema["properties"]
    assert "Profile fields:" in text
    concept = [(r["value"], r["raw_value"]) for r in rows if r["dimension"] == "concept_tags"]
    assert concept == [
        ("pharmacist_warning", "Pharmacist Warning"),
        ("otc_sleep_aid_risks", "otc_sleep_aid_risks"),
    ]
    assert {r["dimension"] for r in rows} >= {"duration_s", "cuts_per_10s", "aspect_ratio"}
    assert await db.fetchval("SELECT count(*) FROM tag_run") == 0
    assert await db.fetchval("SELECT count(*) FROM tag") == 0
    assert await db.fetchval("SELECT count(*) FROM tag_vocab") == 0
