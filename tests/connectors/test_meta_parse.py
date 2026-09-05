import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cs.connectors.meta import parse

FX = Path(__file__).resolve().parents[1] / "fixtures/meta"


def test_creative_image():
    c = parse.creative_from_graph(json.loads((FX / "creative_image.json").read_text()))
    assert c.format == "image" and c.headline == "Sleep better"
    assert c.primary_text == "Tired of waking up tired?" and c.cta_type == "SHOP_NOW"
    assert c.asset_url == "https://scontent.example/img5001.jpg" and c.asset_mime == "image/jpeg"


def test_creative_video_needs_source_lookup():
    c = parse.creative_from_graph(json.loads((FX / "creative_video.json").read_text()))
    assert c.format == "video" and c.asset_url is None and c.raw["video_id"] == "7002"
    assert c.link_url == "https://example.com/v" and c.cta_type == "LEARN_MORE"


def test_metric_from_insight():
    row = json.loads((FX / "insights_page1.json").read_text())["data"][0]
    m = parse.metric_from_insight(row)
    assert m.platform_ad_id == "1001" and m.day == date(2026, 9, 1)
    assert m.placement == "facebook:feed" and m.spend == Decimal("12.50")
    assert m.conversions == Decimal("2") and m.conversion_value == Decimal("90.00")
    assert (m.video_3s_views, m.video_thruplay, m.video_p50, m.video_p100) == (400, 120, 150, 60)


@pytest.mark.parametrize("name", [p.name for p in FX.glob("recorded_creative_*.json")])
def test_recorded_creatives_parse(name):
    c = parse.creative_from_graph(json.loads((FX / name).read_text()))
    assert c.format in ("image", "video", "carousel") and c.platform_creative_id
