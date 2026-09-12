from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

Format = Literal["image", "video", "carousel", "unresolved"]


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
