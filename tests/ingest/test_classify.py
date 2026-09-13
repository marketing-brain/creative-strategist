import json

from cs.ingest.fivetran.classify import (
    IMAGE_URL_MIN_IMPRESSIONS,
    Classification,
    carousel_urls,
    classify,
    stored_verdict,
)

EMPTY = {
    "videos_by_id": {},
    "afs_video_creative_ids": set(),
    "video_view_creative_ids": set(),
    "impressions_by_creative_id": None,
    "feed_spec_image_urls": {},
    "full_watch_creative_ids": None,
}


def _row(**over):
    row = {
        "id": "cr1", "object_type": None, "video_id": None, "video_video_id": None,
        "image_url": None, "thumbnail_url": None,
        "object_story_link_data_child_attachments": None,
    }
    row.update(over)
    return row


def _classify(row, **lookups):
    return classify(row, **{**EMPTY, **lookups})


def test_object_type_video_is_video():
    r = _classify(_row(object_type="VIDEO", video_video_id="v1"), videos_by_id={"v1": "https://cdn/v.mp4"})
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", "https://cdn/v.mp4")


def test_object_type_video_without_resolvable_source_is_still_video():
    r = _classify(_row(object_type="VIDEO", video_video_id="missing"))
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", None)


def test_object_type_photo_is_image():
    r = _classify(_row(object_type="PHOTO", image_url="https://cdn/i.jpg"))
    assert (r.format, r.source, r.asset_url) == ("image", "meta_object_type", "https://cdn/i.jpg")


def test_child_attachments_are_carousel():
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}, {"picture": "https://cdn/b.jpg"}])
    r = _classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw))
    assert (r.format, r.source) == ("carousel", "carousel_attachments")
    assert r.child_urls == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_carousel_outranks_video_view_metric():
    # A carousel holding one video card earns video_views. It is still a carousel.
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}])
    r = _classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw),
                  video_view_creative_ids={"cr1"})
    assert r.format == "carousel"


def test_resolvable_video_id_is_video():
    r = _classify(_row(object_type="SHARE", video_video_id="v9"), videos_by_id={"v9": "https://cdn/v9.mp4"})
    assert (r.format, r.source, r.asset_url) == ("video", "meta_video_id", "https://cdn/v9.mp4")


def test_asset_feed_spec_membership_is_video():
    r = _classify(_row(object_type="SHARE"), afs_video_creative_ids={"cr1"})
    assert (r.format, r.source) == ("video", "asset_feed_spec_video")


def test_video_view_metric_is_video():
    r = _classify(_row(object_type="SHARE"), video_view_creative_ids={"cr1"})
    assert (r.format, r.source) == ("video", "video_view_metric")


def test_share_with_no_evidence_is_unresolved_not_image():
    # The regression test that matters: this is the exact shape that used to
    # download a video preview frame and label it an image.
    r = _classify(_row(object_type="SHARE", thumbnail_url="https://cdn/frame.jpg"))
    assert (r.format, r.source, r.asset_url) == ("unresolved", "unresolved", None)


def test_unknown_object_type_is_unresolved():
    r = _classify(_row(object_type="PRIVACY_CHECK_FAIL", thumbnail_url="https://cdn/f.jpg"))
    assert r.format == "unresolved"


def test_missing_object_type_is_unresolved():
    r = _classify(_row(image_url="https://cdn/i.jpg"))
    assert r.format == "unresolved"


# --- placement asset customization: the uploaded assets are the evidence ---


def test_feed_spec_images_are_an_image_with_the_full_size_library_file():
    r = _classify(_row(object_type="SHARE", thumbnail_url="https://cdn/crop600.jpg"),
                  feed_spec_image_urls={"cr1": "https://cdn/full.jpg"})
    assert (r.format, r.source, r.asset_url) == ("image", "asset_feed_spec_image", "https://cdn/full.jpg")


def test_feed_spec_image_missing_from_the_library_is_still_an_image_but_never_the_thumbnail():
    r = _classify(_row(object_type="SHARE", thumbnail_url="https://cdn/crop600.jpg"),
                  feed_spec_image_urls={"cr1": None})
    assert (r.format, r.source, r.asset_url) == ("image", "asset_feed_spec_image", None)


def test_feed_spec_images_outrank_stray_video_views():
    # Two image-only placement creatives in org 2 carry 5 and 39 views: Meta animates stills.
    r = _classify(_row(object_type="SHARE"), feed_spec_image_urls={"cr1": "https://cdn/full.jpg"},
                  video_view_creative_ids={"cr1"}, full_watch_creative_ids=set())
    assert (r.format, r.source) == ("image", "asset_feed_spec_image")


def test_feed_spec_videos_outrank_feed_spec_images():
    r = _classify(_row(object_type="SHARE"), afs_video_creative_ids={"cr1"},
                  feed_spec_image_urls={"cr1": "https://cdn/full.jpg"})
    assert (r.format, r.source) == ("video", "asset_feed_spec_video")


# --- full watches and the image_url rule ---


def _share(*, impressions=5_000, full_watch=False, views=False, full_watch_data=True, **row):
    """A boosted post with impression and retention data available unless told otherwise."""
    return _classify(
        _row(object_type="SHARE", **row),
        video_view_creative_ids={"cr1"} if views else set(),
        impressions_by_creative_id=None if impressions is None else {"cr1": impressions},
        full_watch_creative_ids=({"cr1"} if full_watch else set()) if full_watch_data else None,
    )


def test_a_full_watch_is_video():
    r = _share(full_watch=True, image_url="https://cdn/i.jpg")
    assert (r.format, r.source) == ("video", "video_full_watch")


def test_image_url_that_served_without_a_full_watch_is_image():
    r = _share(image_url="https://cdn/i.jpg", thumbnail_url="https://cdn/t.jpg")
    assert (r.format, r.source, r.asset_url) == ("image", "image_url", "https://cdn/i.jpg")


def test_image_url_with_stray_views_but_no_full_watch_is_image():
    r = _share(views=True, image_url="https://cdn/i.jpg")
    assert (r.format, r.source) == ("image", "image_url")


def test_image_url_at_the_impression_floor_is_image():
    r = _share(impressions=IMAGE_URL_MIN_IMPRESSIONS, image_url="https://cdn/i.jpg")
    assert r.format == "image"


def test_image_url_below_the_impression_floor_is_not_an_image():
    # Too little delivery for a video to have shown a full watch.
    r = _share(impressions=IMAGE_URL_MIN_IMPRESSIONS - 1, image_url="https://cdn/i.jpg")
    assert r.format == "unresolved"


def test_image_url_below_the_floor_with_views_is_video():
    r = _share(impressions=10, views=True, image_url="https://cdn/i.jpg")
    assert (r.format, r.source) == ("video", "video_view_metric")


def test_image_url_without_retention_data_is_not_an_image():
    # No retention report means no veto, so the image_url alone proves nothing.
    r = _share(full_watch_data=False, image_url="https://cdn/i.jpg")
    assert r.format == "unresolved"


def test_image_url_without_impression_data_is_not_an_image():
    assert _share(impressions=None, image_url="https://cdn/i.jpg").format == "unresolved"


def test_served_post_with_only_a_thumbnail_is_not_an_image():
    # An image needs an image file; the cropped 600px thumbnail is not one.
    r = _share(thumbnail_url="https://cdn/t.jpg")
    assert (r.format, r.source, r.asset_url) == ("unresolved", "unresolved", None)


def test_image_url_rule_only_applies_to_boosted_posts():
    r = _classify(_row(object_type="PRIVACY_CHECK_FAIL", image_url="https://cdn/i.jpg"),
                  impressions_by_creative_id={"cr1": 100_000}, full_watch_creative_ids=set())
    assert r.format == "unresolved"


# --- image and video labels are only stored once their file is ---


def test_a_video_verdict_is_stored_once_its_video_file_is():
    v = Classification("video", "asset_feed_spec_video")
    assert stored_verdict(v, "original_video") == ("video", "asset_feed_spec_video")


def test_a_video_verdict_without_its_file_is_stored_unresolved():
    v = Classification("video", "meta_object_type")
    assert stored_verdict(v, None) == ("unresolved", "unresolved")


def test_a_file_of_the_wrong_kind_does_not_count():
    # A stale image file on a creative now proven to be a video is not its video file.
    v = Classification("video", "video_view_metric")
    assert stored_verdict(v, "original_image") == ("unresolved", "unresolved")


def test_an_image_verdict_needs_its_image_file():
    v = Classification("image", "image_url", "https://cdn/i.jpg")
    assert stored_verdict(v, None) == ("unresolved", "unresolved")
    assert stored_verdict(v, "original_image") == ("image", "image_url")


def test_carousel_and_unresolved_verdicts_are_stored_as_they_are():
    assert stored_verdict(Classification("carousel", "carousel_attachments"), None) == (
        "carousel", "carousel_attachments"
    )
    assert stored_verdict(Classification("unresolved", "unresolved"), None) == (
        "unresolved", "unresolved"
    )


def test_carousel_urls_returns_none_for_malformed_shapes():
    """Regression test: a valid-but-non-list JSON value (e.g. a bare number) must return
    None gracefully instead of raising a TypeError out of the list comprehension."""
    assert carousel_urls(None) is None
    assert carousel_urls("") is None
    assert carousel_urls("not json") is None
    assert carousel_urls("42") is None
    assert carousel_urls('"a string"') is None
    assert carousel_urls("[]") is None
    assert carousel_urls('[{"picture": "https://x/1.jpg"}]') == ["https://x/1.jpg"]
