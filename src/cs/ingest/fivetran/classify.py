from __future__ import annotations

import json
from collections.abc import Container, Mapping
from dataclasses import dataclass

# Delivery needed before a missing full watch can veto an image_url creative being a video.
IMAGE_URL_MIN_IMPRESSIONS = 50


@dataclass(frozen=True)
class Classification:
    format: str
    source: str
    asset_url: str | None = None
    child_urls: list[str] | None = None


# The file an image or video verdict must have before that label is stored.
FILE_KIND = {"image": "original_image", "video": "original_video"}


def stored_verdict(verdict: Classification, primary_asset_kind: str | None) -> tuple[str, str]:
    """(format, format_source) to store. image and video are only stored once the
    creative's primary file is of that kind; until then the creative is unresolved."""
    kind = FILE_KIND.get(verdict.format)
    if kind is None or primary_asset_kind == kind:
        return verdict.format, verdict.source
    return "unresolved", "unresolved"


def _pid(value: object) -> str | None:
    return None if value is None else str(value)


def carousel_urls(raw: str | None) -> list[str] | None:
    """object_story_link_data_child_attachments -> picture URLs."""
    if not raw:
        return None
    try:
        cards = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(cards, list):
        return None
    urls = [c["picture"] for c in cards if isinstance(c, dict) and c.get("picture")]
    return urls or None


def classify(
    row: Mapping,
    *,
    videos_by_id: Mapping[str, str],
    afs_video_creative_ids: Container[str],
    video_view_creative_ids: Container[str],
    impressions_by_creative_id: Mapping[str, int] | None,
    feed_spec_image_urls: Mapping[str, str | None],
    full_watch_creative_ids: Container[str] | None,
) -> Classification:
    """Ordered evidence rules. There is deliberately no default branch: anything
    unproven returns 'unresolved' rather than falling through to 'image'.

    An image verdict needs an image file (a feed-spec image or image_url), never a
    thumbnail. feed_spec_image_urls maps a creative with feed-spec images to its
    full-size library URL (None when the hash isn't in the library)."""
    creative_id = _pid(row["id"])
    object_type = row["object_type"]
    video_ref = _pid(row["video_video_id"] or row["video_id"])

    if object_type == "VIDEO":
        return Classification("video", "meta_object_type", videos_by_id.get(video_ref or ""))
    if object_type == "PHOTO":
        return Classification("image", "meta_object_type", row["image_url"] or row["thumbnail_url"])

    # Structural evidence of a carousel outranks every video rule below: a carousel
    # holding one video card earns video_views without ceasing to be a carousel.
    children = carousel_urls(row["object_story_link_data_child_attachments"])
    if children:
        return Classification("carousel", "carousel_attachments", None, children)

    if video_ref and video_ref in videos_by_id:
        return Classification("video", "meta_video_id", videos_by_id[video_ref])
    if creative_id in afs_video_creative_ids:
        return Classification("video", "asset_feed_spec_video")

    # Uploaded placement assets outrank delivery metrics: Meta animates stills, so
    # image-only placement creatives can carry a handful of views.
    if creative_id is not None and creative_id in feed_spec_image_urls:
        return Classification("image", "asset_feed_spec_image", feed_spec_image_urls[creative_id])

    # A full watch never appears on an image-only creative, so it proves a video is served.
    if full_watch_creative_ids is not None and creative_id in full_watch_creative_ids:
        return Classification("video", "video_full_watch")

    # image_url alone is not proof: link ads can promote a video post. Only enough delivery
    # with retention data synced makes the absence of a full watch meaningful.
    if (
        object_type == "SHARE"
        and row["image_url"]
        and full_watch_creative_ids is not None
        and impressions_by_creative_id is not None
        and impressions_by_creative_id.get(creative_id or "", 0) >= IMAGE_URL_MIN_IMPRESSIONS
    ):
        return Classification("image", "image_url", row["image_url"])

    if creative_id in video_view_creative_ids:
        return Classification("video", "video_view_metric")

    return Classification("unresolved", "unresolved")
