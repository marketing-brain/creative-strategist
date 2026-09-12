from __future__ import annotations

import json
from collections.abc import Container, Mapping
from dataclasses import dataclass

# Calibrated on org 2: no object_type=VIDEO creative with >=50 impressions has zero views.
NO_VIDEO_VIEWS_MIN_IMPRESSIONS = 50


@dataclass(frozen=True)
class Classification:
    format: str
    source: str
    asset_url: str | None = None
    child_urls: list[str] | None = None


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
) -> Classification:
    """Ordered evidence rules. There is deliberately no default branch: anything
    unproven returns 'unresolved' rather than falling through to 'image'."""
    creative_id = _pid(row["id"])
    object_type = row["object_type"]
    video_ref = _pid(row["video_video_id"] or row["video_id"])

    if object_type == "VIDEO":
        return Classification("video", "meta_object_type", videos_by_id.get(video_ref or ""))
    if object_type == "PHOTO":
        return Classification(
            "image", "meta_object_type", row["image_url"] or row["thumbnail_url"]
        )

    # Structural evidence of a carousel outranks every video rule below: a carousel
    # holding one video card earns video_views without ceasing to be a carousel.
    children = carousel_urls(row["object_story_link_data_child_attachments"])
    if children:
        return Classification("carousel", "carousel_attachments", None, children)

    if video_ref and video_ref in videos_by_id:
        return Classification("video", "meta_video_id", videos_by_id[video_ref])
    if creative_id in afs_video_creative_ids:
        return Classification("video", "asset_feed_spec_video")
    if creative_id in video_view_creative_ids:
        return Classification("video", "video_view_metric")

    # Zero views proves "not a video" only if view data synced; None must never yield image.
    if (
        object_type == "SHARE"
        and impressions_by_creative_id is not None
        and impressions_by_creative_id.get(creative_id or "", 0) >= NO_VIDEO_VIEWS_MIN_IMPRESSIONS
    ):
        return Classification("image", "no_video_views", row["image_url"] or row["thumbnail_url"])

    return Classification("unresolved", "unresolved")
