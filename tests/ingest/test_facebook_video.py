import json

import httpx
import pytest
import respx

from cs.ingest import facebook_video

APIFY_URL = (
    "https://api.apify.com/v2/acts/convertfleetdotonline~facebook-downloader/"
    "run-sync-get-dataset-items"
)


@respx.mock
async def test_resolve_video_urls_maps_each_playable_video_id_to_its_file(settings):
    settings.apify_api_key = "test-token"
    route = respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[
            {"input_url": "https://www.facebook.com/watch/?v=111",
             "best_combined_url": "https://video.fbcdn/111-av.mp4",
             "best_video_url": "https://video.fbcdn/111-v.mp4"},
            {"input_url": "https://www.facebook.com/watch/?v=222",
             "error": "yt-dlp failed: Cannot parse data"},
        ])
    )
    async with httpx.AsyncClient() as http:
        urls = await facebook_video.resolve_video_urls(http, settings, ["111", "222"])

    assert urls == {"111": "https://video.fbcdn/111-av.mp4"}
    sent = json.loads(route.calls.last.request.content)
    assert sent["videoUrls"] == [
        "https://www.facebook.com/watch/?v=111", "https://www.facebook.com/watch/?v=222"
    ]


@respx.mock
async def test_resolve_video_urls_falls_back_to_the_video_only_stream(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[
            {"input_url": "https://www.facebook.com/watch/?v=111",
             "best_video_url": "https://video.fbcdn/111-v.mp4"},
        ])
    )
    async with httpx.AsyncClient() as http:
        urls = await facebook_video.resolve_video_urls(http, settings, ["111"])
    assert urls == {"111": "https://video.fbcdn/111-v.mp4"}


@respx.mock
async def test_resolve_video_urls_ignores_items_for_ids_it_did_not_ask_for(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[
            {"input_url": "https://www.facebook.com/watch/?v=999",
             "best_combined_url": "https://video.fbcdn/999.mp4"},
        ])
    )
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
