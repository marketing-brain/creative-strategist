import asyncpg
import pytest

from cs.core.ids import new_id


async def _insert(db, org_id, account_id, *, fmt, source):
    await db.execute(
        "INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id,"
        " format, format_source, raw_path) VALUES ($1, $2, $3, $4, $5, $6, '')",
        new_id(), org_id, account_id, new_id().hex, fmt, source,
    )


async def test_unresolved_requires_unresolved_source(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="unresolved", source="meta_object_type")


async def test_resolved_format_cannot_claim_unresolved_source(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="image", source="unresolved")


async def test_unknown_source_is_rejected(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db, org_id, ad_account_id, fmt="image", source="vibes")


async def test_valid_combinations_are_accepted(db, org_id, ad_account_id):
    await _insert(db, org_id, ad_account_id, fmt="unresolved", source="unresolved")
    await _insert(db, org_id, ad_account_id, fmt="video", source="video_view_metric")


async def test_zero_video_views_image_is_storable(db, org_id, ad_account_id):
    await _insert(db, org_id, ad_account_id, fmt="image", source="no_video_views")


async def test_format_source_has_no_default(db, org_id, ad_account_id):
    with pytest.raises(asyncpg.NotNullViolationError):
        await db.execute(
            "INSERT INTO creative (id, org_id, ad_account_id, platform_creative_id,"
            " format, raw_path) VALUES ($1, $2, $3, $4, $5, '')",
            new_id(), org_id, ad_account_id, new_id().hex, "image",
        )
