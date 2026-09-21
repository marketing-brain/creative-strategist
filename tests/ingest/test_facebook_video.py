import json

import httpx
import pytest
import respx

from cs.ingest import facebook_video

START_URL = "https://api.apify.com/v2/acts/automation-lab~facebook-public-video-downloader/runs"
RUN_URL = "https://api.apify.com/v2/actor-runs/run1"
ITEMS_URL = "https://api.apify.com/v2/datasets/ds1/items"


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


def run(status: str) -> httpx.Response:
    return httpx.Response(
        201, json={"data": {"id": "run1", "status": status, "defaultDatasetId": "ds1"}}
    )


@respx.mock
async def test_resolve_video_urls_maps_each_playable_video_id_to_its_file(settings):
    settings.apify_api_key = "test-token"
    start = respx.post(START_URL).mock(return_value=run("SUCCEEDED"))
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(
            200, json=[resolved("111", "https://video.fbcdn/111.mp4"), failed("222")]
        )
    )
    async with httpx.AsyncClient() as http:
        urls = await facebook_video.resolve_video_urls(http, settings, ["111", "222"])

    assert urls == {"111": "https://video.fbcdn/111.mp4"}
    sent = json.loads(start.calls.last.request.content)
    assert sent["startUrls"] == [
        {"url": "https://www.facebook.com/watch/?v=111"},
        {"url": "https://www.facebook.com/watch/?v=222"},
    ]
    assert sent["saveVideos"] is False
    assert sent["maxItems"] >= 2


@respx.mock
async def test_a_failed_run_that_answered_every_url_means_no_video_plays(settings):
    # The real case: the actor exits non-zero when nothing resolved, but it still wrote a
    # failed item per URL. That is a definitive answer, not a reason to pay for a retry.
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run("FAILED"))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[failed("111"), failed("222")]))
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111", "222"]) == {}


@respx.mock
async def test_a_failed_run_missing_an_answer_raises(settings):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run("FAILED"))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[failed("111")]))
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError):
            await facebook_video.resolve_video_urls(http, settings, ["111", "222"])


@pytest.mark.parametrize("status", ["TIMED-OUT", "ABORTED"])
@respx.mock
async def test_a_timed_out_or_aborted_run_raises_even_with_items(settings, status):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run(status))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[failed("111")]))
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError):
            await facebook_video.resolve_video_urls(http, settings, ["111"])


@respx.mock
async def test_waits_for_a_running_run_to_finish(settings):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run("RUNNING"))
    poll = respx.get(RUN_URL).mock(side_effect=[run("RUNNING"), run("SUCCEEDED")])
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[resolved("111", "https://video.fbcdn/111.mp4")])
    )
    async with httpx.AsyncClient() as http:
        urls = await facebook_video.resolve_video_urls(http, settings, ["111"])
    assert urls == {"111": "https://video.fbcdn/111.mp4"}
    assert poll.call_count == 2


@respx.mock
async def test_a_run_still_going_at_the_deadline_is_aborted_and_raises(settings):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run("RUNNING"))
    abort = respx.post(f"{RUN_URL}/abort").mock(return_value=run("ABORTING"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError):
            await facebook_video.resolve_video_urls(http, settings, ["111"], max_wait_seconds=0)
    assert abort.called


@respx.mock
async def test_resolve_video_urls_ignores_items_for_ids_it_did_not_ask_for(settings):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=run("SUCCEEDED"))
    respx.get(ITEMS_URL).mock(
        return_value=httpx.Response(200, json=[resolved("999", "https://video.fbcdn/999.mp4")])
    )
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111"]) == {}


@respx.mock
async def test_resolve_video_urls_ignores_resolved_items_without_a_media_url(settings):
    settings.apify_api_key = "test-token"
    item = resolved("111", "https://video.fbcdn/111.mp4") | {"sourceMediaUrl": None}
    respx.post(START_URL).mock(return_value=run("SUCCEEDED"))
    respx.get(ITEMS_URL).mock(return_value=httpx.Response(200, json=[item]))
    async with httpx.AsyncClient() as http:
        assert await facebook_video.resolve_video_urls(http, settings, ["111"]) == {}


async def test_resolve_video_urls_without_api_key_raises(settings):
    settings.apify_api_key = ""
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError):
            await facebook_video.resolve_video_urls(http, settings, ["111"])


@respx.mock
async def test_resolve_video_urls_http_failure_raises(settings):
    settings.apify_api_key = "test-token"
    respx.post(START_URL).mock(return_value=httpx.Response(503, text="rate limited"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(facebook_video.FacebookVideoError):
            await facebook_video.resolve_video_urls(http, settings, ["111"])
