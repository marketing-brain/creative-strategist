import json

from cs.ingest.fivetran.classify import carousel_urls, classify

EMPTY = {
    "videos_by_id": {},
    "afs_video_creative_ids": set(),
    "video_view_creative_ids": set(),
    "impressions_by_creative_id": None,
}


def _row(**over):
    row = {
        "id": "cr1", "object_type": None, "video_id": None, "video_video_id": None,
        "image_url": None, "thumbnail_url": None,
        "object_story_link_data_child_attachments": None,
    }
    row.update(over)
    return row


def test_object_type_video_is_video():
    r = classify(_row(object_type="VIDEO", video_video_id="v1"),
                 videos_by_id={"v1": "https://cdn/v.mp4"},
                 afs_video_creative_ids=set(), video_view_creative_ids=set(),
                 impressions_by_creative_id=None)
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", "https://cdn/v.mp4")


def test_object_type_video_without_resolvable_source_is_still_video():
    r = classify(_row(object_type="VIDEO", video_video_id="missing"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("video", "meta_object_type", None)


def test_object_type_photo_is_image():
    r = classify(_row(object_type="PHOTO", image_url="https://cdn/i.jpg"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("image", "meta_object_type", "https://cdn/i.jpg")


def test_child_attachments_are_carousel():
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}, {"picture": "https://cdn/b.jpg"}])
    r = classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw), **EMPTY)
    assert (r.format, r.source) == ("carousel", "carousel_attachments")
    assert r.child_urls == ["https://cdn/a.jpg", "https://cdn/b.jpg"]


def test_carousel_outranks_video_view_metric():
    # A carousel holding one video card earns video_views. It is still a carousel.
    raw = json.dumps([{"picture": "https://cdn/a.jpg"}])
    r = classify(_row(object_type="SHARE", object_story_link_data_child_attachments=raw),
                 videos_by_id={}, afs_video_creative_ids=set(),
                 video_view_creative_ids={"cr1"}, impressions_by_creative_id=None)
    assert r.format == "carousel"


def test_resolvable_video_id_is_video():
    r = classify(_row(object_type="SHARE", video_video_id="v9"),
                 videos_by_id={"v9": "https://cdn/v9.mp4"},
                 afs_video_creative_ids=set(), video_view_creative_ids=set(),
                 impressions_by_creative_id=None)
    assert (r.format, r.source, r.asset_url) == ("video", "meta_video_id", "https://cdn/v9.mp4")


def test_asset_feed_spec_membership_is_video():
    r = classify(_row(object_type="SHARE"), videos_by_id={},
                 afs_video_creative_ids={"cr1"}, video_view_creative_ids=set(),
                 impressions_by_creative_id=None)
    assert (r.format, r.source) == ("video", "asset_feed_spec_video")


def test_video_view_metric_is_video():
    r = classify(_row(object_type="SHARE"), videos_by_id={},
                 afs_video_creative_ids=set(), video_view_creative_ids={"cr1"},
                 impressions_by_creative_id=None)
    assert (r.format, r.source) == ("video", "video_view_metric")


def test_share_with_no_evidence_is_unresolved_not_image():
    # The regression test that matters: this is the exact shape that used to
    # download a video preview frame and label it an image.
    r = classify(_row(object_type="SHARE", thumbnail_url="https://cdn/frame.jpg"), **EMPTY)
    assert (r.format, r.source, r.asset_url) == ("unresolved", "unresolved", None)


def test_unknown_object_type_is_unresolved():
    r = classify(_row(object_type="PRIVACY_CHECK_FAIL", thumbnail_url="https://cdn/f.jpg"), **EMPTY)
    assert r.format == "unresolved"


def test_missing_object_type_is_unresolved():
    r = classify(_row(image_url="https://cdn/i.jpg"), **EMPTY)
    assert r.format == "unresolved"


def _share(*, impressions, in_video_views=False, **row):
    """A boosted post with view data available. `impressions` is its total served impressions."""
    return classify(
        _row(object_type="SHARE", **row),
        videos_by_id={},
        afs_video_creative_ids=set(),
        video_view_creative_ids={"cr1"} if in_video_views else set(),
        impressions_by_creative_id={"cr1": impressions},
    )


def test_share_with_zero_views_at_threshold_is_image():
    r = _share(impressions=50, image_url="https://cdn/full.jpg", thumbnail_url="https://cdn/t.jpg")
    assert (r.format, r.source, r.asset_url) == ("image", "no_video_views", "https://cdn/full.jpg")


def test_share_just_below_threshold_stays_unresolved():
    # Below 50, a zero-view post could be a video that simply wasn't watched.
    assert _share(impressions=49).format == "unresolved"


def test_video_views_outrank_the_zero_views_rule():
    # Ordering guard: views are positive proof of video and must win over an inference.
    r = _share(impressions=100_000, in_video_views=True)
    assert (r.format, r.source) == ("video", "video_view_metric")


def test_zero_views_rule_only_applies_to_boosted_posts():
    # The threshold was calibrated on SHARE creatives only; other types stay unproven.
    r = classify(
        _row(object_type="PRIVACY_CHECK_FAIL"),
        videos_by_id={},
        afs_video_creative_ids=set(),
        video_view_creative_ids=set(),
        impressions_by_creative_id={"cr1": 100_000},
    )
    assert r.format == "unresolved"


def test_zero_views_image_falls_back_to_thumbnail_url():
    r = _share(impressions=5_000, thumbnail_url="https://cdn/t.jpg")
    assert (r.format, r.asset_url) == ("image", "https://cdn/t.jpg")


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
