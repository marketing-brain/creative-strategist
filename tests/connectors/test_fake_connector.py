from datetime import date
from decimal import Decimal

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.fakes import FakeConnector


async def test_fake_connector_roundtrip():
    c = FakeConnector()
    c.add_creative(
        PlatformCreative(
            "cr1",
            "image",
            "H",
            "P",
            None,
            "SHOP_NOW",
            "https://x",
            "https://img/1.png",
            "image/png",
            {},
        ),
        b"png",
    )
    c.add_ad(PlatformAd("ad1", "Ad 1", "ACTIVE", "as1", "c1", "cr1"))
    c.add_metric(
        MetricRow(
            "ad1",
            date(2026, 9, 1),
            "facebook:feed",
            Decimal("10"),
            1000,
            20,
            15,
            Decimal("2"),
            Decimal("80"),
            0,
            0,
            0,
            0,
            {},
        )
    )
    assert [a.platform_ad_id for a in await c.list_ads("act_1")] == ["ad1"]
    assert (await c.get_creative("cr1")).headline == "H"
    assert await c.download_asset("https://img/1.png") == (b"png", "image/png")
    assert len(await c.fetch_metrics("act_1", date(2026, 8, 1), date(2026, 9, 30))) == 1
    assert await c.fetch_metrics("act_1", date(2026, 9, 2), date(2026, 9, 30)) == []
