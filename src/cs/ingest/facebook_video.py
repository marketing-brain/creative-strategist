"""Resolves the playable file behind Facebook video ids via an Apify actor that fetches the
public video player. Reaches videos published on the Page (e.g. placement-customization
Reels); videos uploaded only for ads are not publicly served and come back as failed items.

The actor writes one item per URL, but exits non-zero when none of them resolved, so the
run's status alone can't tell "no video plays" from a broken run. The run is started
asynchronously and its dataset is read whatever the status: a run that answered every URL
is a definitive result; anything short of that is retryable. Failed URLs aren't charged,
but every run pays a start fee, so retrying a definitive answer would waste it."""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

import httpx

from cs.core.config import Settings

ACTOR = "automation-lab~facebook-public-video-downloader"
_API = "https://api.apify.com/v2"
_START_URL = f"{_API}/acts/{ACTOR}/runs"
_WAIT_SECONDS = 60  # Apify's per-request maximum for waitForFinish
_MAX_WAIT_SECONDS = 300
_RUNNING = {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}


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


def _run_data(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    data = resp.json().get("data")
    if not isinstance(data, dict) or not data.get("id"):
        raise ValueError("Apify returned no run")
    return data


async def resolve_video_urls(
    http: httpx.AsyncClient,
    settings: Settings,
    video_ids: list[str],
    *,
    max_wait_seconds: float = _MAX_WAIT_SECONDS,
) -> dict[str, str]:
    """One actor run for all ids; returns {video_id: file URL} for those that play.
    sourceMediaUrl is the actor's single best progressive MP4 (video with audio)."""
    if not settings.apify_api_key:
        raise FacebookVideoError("APIFY_API_KEY is not configured", retryable=False)
    if not video_ids:
        return {}
    headers = {"Authorization": f"Bearer {settings.apify_api_key}"}
    deadline = time.monotonic() + max_wait_seconds
    try:
        run = _run_data(
            await http.post(
                _START_URL,
                headers=headers,
                params={"waitForFinish": _WAIT_SECONDS},
                json={
                    "startUrls": [{"url": watch_url(v)} for v in video_ids],
                    "saveVideos": False,
                    "preferredQuality": "best",
                    "maxItems": len(video_ids),
                },
                timeout=_WAIT_SECONDS + 30,
            )
        )
        run_url = f"{_API}/actor-runs/{run['id']}"
        while run.get("status") in _RUNNING and time.monotonic() < deadline:
            run = _run_data(
                await http.get(
                    run_url,
                    headers=headers,
                    params={"waitForFinish": _WAIT_SECONDS},
                    timeout=_WAIT_SECONDS + 30,
                )
            )
        if run.get("status") in _RUNNING:
            # Stop paying for a run nobody will read.
            try:
                await http.post(f"{run_url}/abort", headers=headers, timeout=30)
            except httpx.HTTPError:
                pass
            raise FacebookVideoError(
                f"Apify run {run['id']} still {run['status']} after {max_wait_seconds}s",
                retryable=True,
            )
        resp = await http.get(
            f"{_API}/datasets/{run['defaultDatasetId']}/items",
            headers=headers,
            params={"clean": "true"},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except FacebookVideoError:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise FacebookVideoError(f"Apify request failed: {exc}", retryable=True) from exc
    if not isinstance(data, list):
        raise FacebookVideoError("Apify returned a non-list response", retryable=True)

    wanted = set(video_ids)
    answered: set[str] = set()
    urls: dict[str, str] = {}
    for item in data:
        if not isinstance(item, dict):
            continue
        vid = _video_id(item.get("sourceUrl"))
        if vid not in wanted:
            continue
        status = item.get("retrievalStatus")
        if status in ("resolved", "failed"):
            answered.add(vid)
        url = item.get("sourceMediaUrl")
        if status == "resolved" and isinstance(url, str) and url:
            urls[vid] = url

    finished = run.get("status") == "SUCCEEDED" or (
        run.get("status") == "FAILED" and answered == wanted
    )
    if not finished:
        raise FacebookVideoError(
            f"Apify run {run['id']} ended {run.get('status')} with "
            f"{len(answered)}/{len(wanted)} URLs answered: {run.get('statusMessage')}",
            retryable=True,
        )
    return urls
