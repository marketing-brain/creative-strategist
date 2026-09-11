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
    """retryable=True for anything that looks transient (network error, rate limit,
    malformed response) -- the caller should raise and let the job queue retry with
    backoff. retryable=False for a definitive answer (post deleted/private/not a video,
    or the feature isn't configured) -- retrying that on the next sync would just spend
    money to get the same answer again, so the caller should give up instead."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


async def resolve_video_url(http: httpx.AsyncClient, settings: Settings, permalink_url: str) -> str:
    if not settings.apify_api_key:
        raise InstagramScrapeError("APIFY_API_KEY is not configured", retryable=False)
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
        raise InstagramScrapeError(f"Apify request failed: {exc}", retryable=True) from exc
    if not isinstance(data, list) or not data:
        raise InstagramScrapeError("Apify returned no items for this URL", retryable=False)
    video_url = data[0].get("videoUrl")
    if not video_url:
        raise InstagramScrapeError(
            "Apify item has no videoUrl -- not a video post", retryable=False
        )
    return video_url
