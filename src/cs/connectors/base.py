from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

Format = Literal["image", "video", "carousel"]


@dataclass
class PlatformAd:
    platform_ad_id: str
    name: str
    status: str
    adset_id: str | None
    campaign_id: str | None
    platform_creative_id: str


@dataclass
class PlatformCreative:
    platform_creative_id: str
    format: Format
    headline: str | None
    primary_text: str | None
    description: str | None
    cta_type: str | None
    link_url: str | None
    asset_url: str | None
    asset_mime: str | None
    raw: dict = field(default_factory=dict)
    child_asset_urls: list[str] | None = None


@dataclass
class MetricRow:
    platform_ad_id: str
    day: date
    placement: str
    spend: Decimal
    impressions: int
    clicks: int
    link_clicks: int
    conversions: Decimal
    conversion_value: Decimal
    video_3s_views: int
    video_thruplay: int
    video_p50: int
    video_p100: int
    raw: dict = field(default_factory=dict)
    # Diagnosis-panel columns (spec 2026-09-12). Appended after `raw` so positional
    # constructors in older code and tests keep working.
    reach: int = 0
    video_plays: int = 0
    video_p25: int = 0
    video_p75: int = 0
    video_p95: int = 0
    video_watch_seconds: Decimal = Decimal(0)
    landing_page_views: int = 0
    content_views: int = 0
    add_to_carts: int = 0
    checkouts: int = 0
    purchases: int = 0
    post_reactions: int = 0
    comments: int = 0
    saves: int = 0
