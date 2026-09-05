import json
from datetime import date
from pathlib import Path

import httpx
import respx

from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector

FX = Path(__file__).resolve().parents[1] / "fixtures/meta"
G = "https://graph.facebook.com/v21.0"


@respx.mock
async def test_get_all_follows_paging_and_sinks_raw(settings):
    page1 = json.loads((FX / "ads_page1.json").read_text())
    page1["paging"]["next"] = f"{G}/act_1/ads?after=x"
    respx.get(f"{G}/act_1/ads").mock(
        side_effect=[
            httpx.Response(200, json=page1),
            httpx.Response(200, json={"data": [{"id": "1003", "creative": {"id": "5003"}}]}),
        ]
    )
    sunk = []

    async def sink(name, page):
        sunk.append(name)

    async with httpx.AsyncClient() as http:
        gc = GraphClient(settings, "tok", http, raw_sink=sink)
        rows = await gc.get_all("act_1/ads", {"fields": "id"})
    assert [r["id"] for r in rows] == ["1001", "1002", "1003"] and sunk == [
        "act_1/ads",
        "act_1/ads",
    ]


@respx.mock
async def test_rate_limit_retries(settings, monkeypatch):
    calls = []
    respx.get(f"{G}/act_1/ads").mock(
        side_effect=[
            httpx.Response(
                400, json={"error": {"code": 17, "message": "User request limit reached"}}
            ),
            httpx.Response(200, json={"data": []}),
        ]
    )
    import cs.connectors.meta.client as mod

    async def fake_sleep(s):
        calls.append(s)

    monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
    async with httpx.AsyncClient() as http:
        rows = await GraphClient(settings, "tok", http).get_all("act_1/ads", {})
    assert rows == [] and calls and calls[0] >= 30


@respx.mock
async def test_connector_video_creative_resolves_source(settings):
    respx.get(f"{G}/5002").mock(
        return_value=httpx.Response(200, json=json.loads((FX / "creative_video.json").read_text()))
    )
    respx.get(f"{G}/7002").mock(
        return_value=httpx.Response(200, json={"source": "https://video.example/7002.mp4"})
    )
    async with httpx.AsyncClient() as http:
        c = await MetaConnector(GraphClient(settings, "tok", http)).get_creative("5002")
    assert c.asset_url == "https://video.example/7002.mp4" and c.asset_mime == "video/mp4"


@respx.mock
async def test_connector_fetch_metrics(settings):
    respx.get(f"{G}/act_1/insights").mock(
        return_value=httpx.Response(200, json=json.loads((FX / "insights_page1.json").read_text()))
    )
    async with httpx.AsyncClient() as http:
        rows = await MetaConnector(GraphClient(settings, "tok", http)).fetch_metrics(
            "1", date(2026, 9, 1), date(2026, 9, 1)
        )
    assert len(rows) == 1 and rows[0].placement == "facebook:feed"
