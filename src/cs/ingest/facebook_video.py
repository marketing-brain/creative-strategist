"""Resolves the playable file behind Facebook video ids via an Apify actor that fetches the
public video player. Reaches videos published on the Page (e.g. placement-customization
Reels); videos uploaded only for ads are not publicly served and come back as failed items.

The actor reports each URL as its own item and the run succeeds even when every URL
fails, so an unplayable video is an answer rather than an error, and failed URLs are not
charged."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx

from cs.core.config import Settings

ACTOR = "automation-lab~facebook-public-video-downloader"
_APIFY_RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"


class FacebookVideoError(Exception):
    """retryable=True for transient failures (network, rate limit, malformed response);
    False for a definitive answer, so the caller gives up instead of paying to re-ask."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def watch_url(video_id: str) -> str:
    return f"https://www.facebook.com/watch/?v={video_id}"


def _video_id(input_url: object) -> str | None:
    if not isinstance(input_url, str):
        return None
    return (parse_qs(urlparse(input_url).query).get("v") or [None])[0]


async def resolve_video_urls(
    http: httpx.AsyncClient, settings: Settings, video_ids: list[str]
) -> dict[str, str]:
    """One actor run for all ids; returns {video_id: file URL} for those that play.
    sourceMediaUrl is the actor's single best progressive MP4 (video with audio)."""
    if not settings.apify_api_key:
        raise FacebookVideoError("APIFY_API_KEY is not configured", retryable=False)
    if not video_ids:
        return {}
    try:
        resp = await http.post(
            _APIFY_RUN_URL,
            headers={"Authorization": f"Bearer {settings.apify_api_key}"},
            json={
                "startUrls": [{"url": watch_url(v)} for v in video_ids],
                "saveVideos": False,
                "preferredQuality": "best",
                "maxItems": len(video_ids),
            },
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise FacebookVideoError(f"Apify request failed: {exc}", retryable=True) from exc
    if not isinstance(data, list):
        raise FacebookVideoError("Apify returned a non-list response", retryable=True)
    wanted = set(video_ids)
    urls: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("retrievalStatus") != "resolved":
            continue
        vid = _video_id(item.get("sourceUrl"))
        url = item.get("sourceMediaUrl")
        if vid in wanted and isinstance(url, str) and url:
            urls[vid] = url
    return urls
