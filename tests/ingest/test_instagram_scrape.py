import httpx
import pytest
import respx

from cs.ingest import instagram_scrape

APIFY_URL = "https://api.apify.com/v2/acts/apify~instagram-scraper/run-sync-get-dataset-items"


@respx.mock
async def test_resolve_video_url_returns_videourl_from_apify(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[{"videoUrl": "https://cdn.instagram/vid.mp4"}])
    )
    async with httpx.AsyncClient() as http:
        url = await instagram_scrape.resolve_video_url(
            http, settings, "https://www.instagram.com/p/abc123/"
        )
    assert url == "https://cdn.instagram/vid.mp4"


async def test_resolve_video_url_without_api_key_raises(settings):
    settings.apify_api_key = ""
    async with httpx.AsyncClient() as http:
        with pytest.raises(instagram_scrape.InstagramScrapeError):
            await instagram_scrape.resolve_video_url(http, settings, "https://instagram.com/p/x/")


@respx.mock
async def test_resolve_video_url_empty_result_raises(settings):
    """Apify returning [] means it could not find/access the post at all (deleted,
    private, wrong URL) -- a definitive answer, not a transient failure."""
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(return_value=httpx.Response(200, json=[]))
    async with httpx.AsyncClient() as http:
        with pytest.raises(instagram_scrape.InstagramScrapeError):
            await instagram_scrape.resolve_video_url(http, settings, "https://instagram.com/p/x/")


@respx.mock
async def test_resolve_video_url_non_video_post_raises(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(
        return_value=httpx.Response(200, json=[{"displayUrl": "https://cdn.instagram/photo.jpg"}])
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(instagram_scrape.InstagramScrapeError):
            await instagram_scrape.resolve_video_url(http, settings, "https://instagram.com/p/x/")


@respx.mock
async def test_resolve_video_url_http_failure_raises(settings):
    settings.apify_api_key = "test-token"
    respx.post(APIFY_URL).mock(return_value=httpx.Response(503, text="rate limited"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(instagram_scrape.InstagramScrapeError):
            await instagram_scrape.resolve_video_url(http, settings, "https://instagram.com/p/x/")
