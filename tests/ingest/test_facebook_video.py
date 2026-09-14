import json

import httpx
import pytest
import respx

from cs.ingest import facebook_video

APIFY_URL = (
    "https://api.apify.com/v2/acts/automation-lab~facebook-public-video-downloader/"
    "run-sync-get-dataset-items"
)


def resolved(video_id: str, media_url: str) -> dict:
    # Shape of a real item from the actor (run aOcopQey7gvaTVVq2, 2026-09-14).
    return {
        "videoId": video_id,
        "sourceUrl": f"https://www.facebook.com/watch/?v={video_id}",
        "canonicalUrl": f"https://www.facebook.com/watch/?v={video_id}",
        "sourceMediaUrl": media_url,
        "videoFileUrl": None,
        "quality": "hd",
        "format": "mp4",
        "retrievalStatus": "resolved",
        "error": None,
    }


def failed(video_id: str) -> dict:
    return {
        "videoId": None,
        "sourceUrl": f"https://www.facebook.com/watch/?v={video_id}",
        "canonicalUrl": None,
        "sourceMediaUrl": None,
        "videoFileUrl": None,
        "retrievalStatus": "failed",
        "error": f"ERROR: [facebook] {video_id}: Cannot parse data",
    }


@respx.mock
async def test_resolve_video_urls_maps_each_playable_video_id_to_its_file(settings):
    settings.apify_api_key = "test-token"
    route = respx.post(APIFY_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                resolved("111", "https://video.fbcdn/111.mp4"),
                failed("222"),
            ],
        )
    )
    async with httpx.AsyncClient() as http:
        urls = await facebook_video.resolve_video_urls(http, settings, ["111", "222"])

    assert urls == {"111": "https://video.fbcdn/111.mp4"}
    sent = json.loads(route.calls.last.request.content)
    assert sent["startUrls"] == [
        {"url": "https://www.facebook.com/watch/?v=111"},
        {"url": "https://www.facebook.com/watch/?v=222"},
    ]
    assert sent["saveVideos"] is False
    assert sent["maxItems"] >= 2


@respx.mock
async def test_resolve_video_urls_when_no_id_plays_returns_nothing(settings):
    # Unpublished ad-only videos fail every time; that is an answer, not an error.
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[failed("111"), failed("222")])
    )
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111", "222"]) == {}


@respx.mock
async def test_resolve_video_urls_ignores_items_for_ids_it_did_not_ask_for(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[resolved("999", "https://video.fbcdn/999.mp4")])
    )
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111"]) == {}


@respx.mock
async def test_resolve_video_urls_ignores_resolved_items_without_a_media_url(settings):
    settings.apify_api_key = "test-token"
    item = resolved("111", "https://video.fbcdn/111.mp4") | {"sourceMediaUrl": None}
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[item]))
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111"]) == {}


async def test_resolve_video_urls_without_api_key_is_not_retryable(settings):
    settings.apify_api_key = ""
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError) as exc_info:
            await facebook_video.resolve_video_urls(http, settings, ["111"])
    assert exc_info.value.retryable is False


@respx.mock
async def test_resolve_video_urls_http_failure_is_retryable(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(return_value=httpx.Response(503, text="rate limited"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError) as exc_info:
            await facebook_video.resolve_video_urls(http, settings, ["111"])
    assert exc_info.value.retryable is True
