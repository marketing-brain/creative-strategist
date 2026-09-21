"""Resolves the downloadable video behind an Instagram permalink via Apify's hosted
apify/instagram-scraper actor. Only reached for Meta video creatives that could not be
resolved through Fivetran/ad_video_history at all -- Partnership Ads, where the video is
owned by the creator's Instagram account rather than the advertiser's ad-account video
library, a namespace no Meta Marketing API table exposes."""

from __future__ import annotations

import httpx

from cs.core.config import Settings

_APIFY_RUN_URL = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"


class InstagramScrapeError(Exception):
    """Any failure to get a video URL. The caller never retries: every Apify run is paid."""


async def resolve_video_url(http: httpx.AsyncClient, settings: Settings, permalink_url: str) -> str:
    if not settings.apify_api_key:
        raise InstagramScrapeError("APIFY_API_KEY is not configured")
    try:
        resp = await http.post(
            _APIFY_RUN_URL,
            headers={"Authorization": f"Bearer {settings.apify_api_key}"},
            json={"directUrls": [permalink_url], "resultsLimit": 1},
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise InstagramScrapeError(f"Apify request failed: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise InstagramScrapeError("Apify returned no items for this URL")
    video_url = data[0].get("videoUrl")
    if not video_url:
        raise InstagramScrapeError("Apify item has no videoUrl -- not a video post")
    return video_url
