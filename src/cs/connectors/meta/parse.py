from __future__ import annotations

from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformCreative

CONVERSION_ACTION_TYPES = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "lead",
    "complete_registration",
    "onsite_conversion.lead_grouped",
)


def _first_action(items: list[dict] | None, types: tuple[str, ...]) -> Decimal:
    by_type = {i["action_type"]: Decimal(str(i["value"])) for i in (items or [])}
    for t in types:
        if t in by_type:
            return by_type[t]
    return Decimal(0)


def _video_count(items: list[dict] | None) -> int:
    return int(sum(Decimal(str(i["value"])) for i in (items or [])))


def _carousel_card_urls(link: dict) -> list[str] | None:
    cards = link.get("child_attachments") or []
    urls = [c["picture"] for c in cards if c.get("picture")]
    return urls or None


def creative_from_graph(obj: dict) -> PlatformCreative:
    spec = obj.get("object_story_spec") or {}
    link = spec.get("link_data") or {}
    video = spec.get("video_data") or {}
    feed = obj.get("asset_feed_spec") or {}

    if obj.get("video_id") or video.get("video_id"):
        fmt = "video"
    elif link.get("child_attachments") or len(feed.get("images", [])) > 1:
        fmt = "carousel"
    else:
        fmt = "image"

    cta = link.get("call_to_action") or video.get("call_to_action") or {}
    link_url = link.get("link") or (cta.get("value") or {}).get("link") or obj.get("link_url")
    card_urls = _carousel_card_urls(link) if fmt == "carousel" else None
    if fmt == "video" or (fmt == "carousel" and card_urls):
        asset_url = None
    else:
        asset_url = obj.get("image_url")
    return PlatformCreative(
        platform_creative_id=str(obj["id"]),
        format=fmt,
        headline=obj.get("title") or link.get("name") or video.get("title"),
        primary_text=obj.get("body") or link.get("message") or video.get("message"),
        description=link.get("description"),
        cta_type=obj.get("call_to_action_type") or cta.get("type"),
        link_url=link_url,
        asset_url=asset_url,
        asset_mime="image/jpeg" if asset_url else None,
        child_asset_urls=card_urls,
        raw={
            "video_id": obj.get("video_id") or video.get("video_id"),
            "thumbnail_url": obj.get("thumbnail_url"),
            **obj,
        },
    )


def metric_from_insight(row: dict) -> MetricRow:
    platform = row.get("publisher_platform", "unknown")
    position = row.get("platform_position", "unknown")
    return MetricRow(
        platform_ad_id=str(row["ad_id"]),
        day=date.fromisoformat(row["date_start"]),
        placement=f"{platform}:{position}",
        spend=Decimal(str(row.get("spend", "0"))),
        impressions=int(row.get("impressions", 0)),
        clicks=int(row.get("clicks", 0)),
        link_clicks=int(row.get("inline_link_clicks", 0)),
        conversions=_first_action(row.get("actions"), CONVERSION_ACTION_TYPES),
        conversion_value=_first_action(row.get("action_values"), CONVERSION_ACTION_TYPES),
        video_3s_views=_video_count(row.get("video_play_actions")),
        video_thruplay=_video_count(row.get("video_thruplay_watched_actions")),
        video_p50=_video_count(row.get("video_p50_watched_actions")),
        video_p100=_video_count(row.get("video_p100_watched_actions")),
        raw=row,
    )
