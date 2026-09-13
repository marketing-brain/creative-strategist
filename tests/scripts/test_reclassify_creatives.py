"""Focused tests for the reclassification backfill's dry-run/decision logic
(build_plan): what verdict changes it detects, that it never mutates on its own,
and that it orders by impressions. Deliberately does NOT exercise apply_plan()'s
mutation/deletion path -- per this task's brief, that path (format writes, asset/
tag/tag_run/embedding deletes, job enqueue) is reviewed by reading the code and
tracing FK order by hand, not by running it here, even against the isolated test
database."""

from uuid import uuid4

from scripts.reclassify_creatives import ReclassifyRow, apply_plan, build_plan

from cs.core import repo
from cs.core.ids import new_id
from cs.ingest.fivetran.fixtures import (
    create_fixture_schema,
    insert_asset_feed_spec_video,
    insert_campaign_ad_creative,
    insert_daily_insight,
    insert_feed_spec_image,
    insert_library_image,
    insert_video,
)
from cs.ingest.fivetran.insights import sync_insights
from cs.ingest.fivetran.transform import discover_and_sync_entities
from cs.jobs.registry import JobContext

SCHEMA = "reclassify_test__meta_ads"


async def _synced_org(db, blobs, settings, org_id):
    """Builds an org with one creative that the *current* (fixed) classifier
    would call video (via a resolvable video_video_id, so verdict.asset_url is
    populated too), then rewrites its stored row back to the old buggy verdict --
    format='image', format_source='legacy_guess' -- exactly what production rows
    look like today."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db,
        SCHEMA,
        campaign_id="c1",
        account_id="999",
        adset_id="as1",
        ad_id="ad1",
        creative_id="cr1",
        name="name",
        headline="Headline",
        image_url=None,
        object_type="SHARE",
        video_video_id="v1",
    )
    await insert_video(db, SCHEMA, video_id="v1", source="https://cdn.example/video.mp4")

    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await discover_and_sync_entities(ctx, org_id, connection)

    creative = await db.fetchrow("SELECT * FROM creative WHERE org_id=$1", org_id)
    # sanity: the classifier proves a video; with no file yet it is stored unresolved
    assert creative["format"] == "unresolved"

    # Roll the row back to what the old `else: image` bug actually wrote to
    # production, including the tell-tale legacy_guess source and a stale
    # "video preview frame" stored as the image asset.
    await db.execute(
        "UPDATE creative SET format='image', format_source='legacy_guess',"
        " format_verified_at=now() WHERE id=$1",
        creative["id"],
    )
    asset_id = await repo.insert_asset(
        db, org_id, creative["id"], "original_image", "frame.jpg", "sha", "image/jpeg"
    )
    await repo.set_primary_asset(db, org_id, creative["id"], asset_id)
    # The sync above enqueued a download job for the (correct) verdict it computed
    # at sync time -- not representative of whatever the old buggy ingest path
    # actually enqueued (an original_image download, if anything). Clear it so
    # job-count assertions below reflect only the simulated legacy state.
    await db.execute("DELETE FROM job WHERE org_id=$1", org_id)
    return creative["id"]


async def test_build_plan_is_read_only_and_flags_the_image_to_video_regression(
    db, blobs, settings, org_id
):
    creative_id = await _synced_org(db, blobs, settings, org_id)

    plan = await build_plan(db, org_id)

    assert len(plan.rows) == 1
    row = plan.rows[0]
    assert row.creative_id == creative_id
    assert (row.old_format, row.old_source) == ("image", "legacy_guess")
    assert (row.verdict_format, row.verdict_source) == ("video", "meta_video_id")
    # Its only file is the stale frame, so the video label waits for the video download.
    assert (row.new_format, row.new_source) == ("unresolved", "unresolved")
    assert row.asset_url == "https://cdn.example/video.mp4"
    assert row.changed and row.is_image_to_video
    assert plan.transition_tally()[("image", "legacy_guess", "unresolved", "unresolved")] == 1
    assert plan.no_ads == 0  # the fixture creative does have an ad

    # Nothing observable changed: build_plan must never write.
    after = await db.fetchrow("SELECT format, format_source FROM creative WHERE id=$1", creative_id)
    assert (after["format"], after["format_source"]) == ("image", "legacy_guess")
    assert await db.fetchval("SELECT count(*) FROM asset WHERE creative_id=$1", creative_id) == 1
    n_jobs = await db.fetchval("SELECT count(*) FROM job WHERE job_type='meta.download_creative'")
    assert n_jobs == 0


async def test_dry_run_never_mutates_even_when_a_verdict_changes(db, blobs, settings, org_id):
    """The single most important property of this script: calling build_plan()
    alone -- the dry-run path -- must never touch the database, no matter what
    it finds. Only apply_plan() (gated behind --apply in run()) may write.

    Crucially, `tag` and `tag_run` must actually be non-empty here: an earlier
    version of this test snapshotted them in a fixture where both were empty,
    so the assertions covering the tables whose deletion is the dangerous part
    of apply_plan compared `[] == []` -- passing for the wrong reason."""
    creative_id = await _synced_org(db, blobs, settings, org_id)
    taxonomy_id = new_id()
    await db.execute(
        "INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, 'v1', 'deadbeef')",
        taxonomy_id,
    )
    run_id = new_id()
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status)"
        " VALUES ($1, $2, $3, $4, 'image-model', 'done')",
        run_id,
        org_id,
        creative_id,
        taxonomy_id,
    )
    await db.execute(
        "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, confidence)"
        " VALUES ($1, $2, $3, $4, 'dim', 'val', 0.9)",
        new_id(),
        org_id,
        run_id,
        creative_id,
    )

    tables = ("creative", "asset", "tag", "tag_run", "job")
    before = {
        t: await db.fetch(f"SELECT * FROM {t} WHERE org_id=$1 ORDER BY id", org_id) for t in tables
    }
    assert before["tag"] and before["tag_run"], "fixture must actually exercise these tables"

    await build_plan(db, org_id)

    for t in tables:
        after = await db.fetch(f"SELECT * FROM {t} WHERE org_id=$1 ORDER BY id", org_id)
        assert [dict(r) for r in after] == [dict(r) for r in before[t]], t


async def test_ordered_by_descending_impressions(db, blobs, settings, org_id):
    """Two creatives, both mislabeled the same way: the higher-spend one (more
    impressions) must be first in the plan so an operator applying manually, or a
    future incremental --apply, fixes the costliest creative first."""
    await create_fixture_schema(db, SCHEMA)
    for cid, ad_id, impressions in (("cr_low", "ad_low", 10), ("cr_high", "ad_high", 5000)):
        await insert_campaign_ad_creative(
            db,
            SCHEMA,
            campaign_id="c1",
            account_id="999",
            adset_id="as1",
            ad_id=ad_id,
            creative_id=cid,
            name=cid,
            headline="H",
            image_url=None,
            object_type="SHARE",
        )
        await insert_asset_feed_spec_video(db, SCHEMA, creative_id=cid, video_id=f"v_{cid}")
        await insert_daily_insight(
            db,
            SCHEMA,
            ad_id=ad_id,
            day="2026-09-01",
            spend=1,
            impressions=impressions,
            link_clicks=0,
        )

    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await discover_and_sync_entities(ctx, org_id, connection)
    await sync_insights(ctx, org_id, connection)

    plan = await build_plan(db, org_id)
    assert [r.platform_creative_id for r in plan.rows] == ["cr_high", "cr_low"]
    assert [r.impressions for r in plan.rows] == [5000, 10]


FEED_IMAGE = ("image", "asset_feed_spec_image")
UNRESOLVED = ("unresolved", "unresolved")


def _row(old, verdict, *, asset_url="https://cdn/full.jpg", primary_kind=None):
    return ReclassifyRow(
        creative_id=uuid4(), platform_creative_id="cr1", impressions=0,
        old_format=old[0], old_source=old[1], verdict_format=verdict[0],
        verdict_source=verdict[1], asset_url=asset_url, primary_asset_kind=primary_kind,
    )


def test_switching_a_thumbnail_image_to_its_feed_spec_image_needs_the_full_size_file():
    row = _row(("image", "no_video_views"), FEED_IMAGE, primary_kind="original_image")
    assert row.needs_image_download
    assert (row.new_format, row.new_source) == UNRESOLVED  # until the full-size file lands


def test_an_image_url_image_already_has_its_file():
    # classify() always preferred image_url, so the stored file is already the full image.
    row = _row(("image", "no_video_views"), ("image", "image_url"), primary_kind="original_image")
    assert not row.needs_image_download
    assert (row.new_format, row.new_source) == ("image", "image_url")


def test_a_new_image_needs_its_file_but_only_when_one_exists():
    assert _row(UNRESOLVED, FEED_IMAGE).needs_image_download
    assert _row(("video", "video_view_metric"), FEED_IMAGE).needs_image_download
    assert not _row(UNRESOLVED, FEED_IMAGE, asset_url=None).needs_image_download


def test_a_video_without_its_file_is_stored_unresolved():
    row = _row(("video", "video_view_metric"), ("video", "video_full_watch"), asset_url=None)
    assert (row.new_format, row.new_source) == UNRESOLVED
    assert row.changed


def test_a_video_with_its_file_keeps_the_label():
    row = _row(("video", "meta_object_type"), ("video", "meta_object_type"), asset_url=None,
               primary_kind="original_video")
    assert (row.new_format, row.new_source) == ("video", "meta_object_type")
    assert not row.changed


async def _thumbnail_image_creative(db, blobs, settings, org_id):
    """A placement creative stored the old way: an image from its cropped thumbnail, tagged."""
    await create_fixture_schema(db, SCHEMA)
    await insert_campaign_ad_creative(
        db, SCHEMA, campaign_id="c1", account_id="999", adset_id="as1", ad_id="ad1",
        creative_id="cr1", name="n", headline="H", image_url=None, object_type="SHARE",
    )
    await insert_feed_spec_image(db, SCHEMA, creative_id="cr1", index=0, hash="h1", label_id="L1")
    await insert_library_image(db, SCHEMA, hash="h1", url="https://cdn/full-1080.jpg")
    conn_id = await repo.upsert_meta_connection(db, org_id, "conn_1", SCHEMA)
    connection = await repo.get_meta_connection_by_id(db, conn_id)
    ctx = JobContext(db=db, blobs=blobs, settings=settings, job=None)
    await discover_and_sync_entities(ctx, org_id, connection)
    accounts = await repo.list_ad_accounts(db, org_id)
    await repo.set_ad_account_status(db, org_id, accounts[0]["id"], "active")
    await discover_and_sync_entities(ctx, org_id, connection)
    creative_id = await db.fetchval("SELECT id FROM creative WHERE org_id=$1", org_id)
    await db.execute(
        "UPDATE creative SET format='image', format_source='no_video_views' WHERE id=$1",
        creative_id,
    )
    asset_id = await repo.insert_asset(
        db, org_id, creative_id, "original_image", "crop600.jpg", "sha", "image/jpeg"
    )
    await repo.set_primary_asset(db, org_id, creative_id, asset_id)
    taxonomy_id = new_id()
    await db.execute(
        "INSERT INTO taxonomy_version (id, version, yaml_sha256) VALUES ($1, 'v1', 'cafe')",
        taxonomy_id,
    )
    run_id = new_id()
    await db.execute(
        "INSERT INTO tag_run (id, org_id, creative_id, taxonomy_version_id, model, status)"
        " VALUES ($1, $2, $3, $4, 'image-model', 'done')",
        run_id, org_id, creative_id, taxonomy_id,
    )
    await db.execute(
        "INSERT INTO tag (id, org_id, tag_run_id, creative_id, dimension, value, confidence)"
        " VALUES ($1, $2, $3, $4, 'dim', 'val', 0.9)",
        new_id(), org_id, run_id, creative_id,
    )
    await db.execute("DELETE FROM job WHERE org_id=$1", org_id)
    return creative_id


async def test_plan_upgrades_a_thumbnail_image_to_its_full_size_feed_spec_image(
    db, blobs, settings, org_id
):
    creative_id = await _thumbnail_image_creative(db, blobs, settings, org_id)

    plan = await build_plan(db, org_id)

    (row,) = plan.rows
    assert row.creative_id == creative_id
    assert (row.verdict_format, row.verdict_source, row.asset_url) == (
        "image", "asset_feed_spec_image", "https://cdn/full-1080.jpg"
    )
    assert row.needs_image_download


async def test_apply_replaces_the_thumbnail_file_and_its_tags_with_a_full_size_download(
    db, blobs, settings, org_id
):
    creative_id = await _thumbnail_image_creative(db, blobs, settings, org_id)

    result = await apply_plan(db, org_id, await build_plan(db, org_id))

    assert result.failed == 0
    assert result.orphaned_asset_paths == ["crop600.jpg"]
    creative = await db.fetchrow("SELECT * FROM creative WHERE id=$1", creative_id)
    # Unresolved until the queued full-size download lands and stores the image label.
    assert (creative["format"], creative["format_source"]) == ("unresolved", "unresolved")
    assert creative["primary_asset_id"] is None
    assert await db.fetchval("SELECT count(*) FROM asset WHERE creative_id=$1", creative_id) == 0
    assert await db.fetchval("SELECT count(*) FROM tag WHERE creative_id=$1", creative_id) == 0
    assert await db.fetchval("SELECT count(*) FROM tag_run WHERE creative_id=$1", creative_id) == 0
    job = await db.fetchrow("SELECT * FROM job WHERE job_type='meta.download_creative'")
    assert (job["payload"]["asset_url"], job["payload"]["kind"]) == (
        "https://cdn/full-1080.jpg", "original_image"
    )
    assert (job["payload"]["format"], job["payload"]["format_source"]) == FEED_IMAGE
