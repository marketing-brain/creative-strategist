from __future__ import annotations

from datetime import date

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative


class FakeConnector:
    def __init__(self) -> None:
        self.ads: list[PlatformAd] = []
        self.creatives: dict[str, PlatformCreative] = {}
        self.assets: dict[str, tuple[bytes, str]] = {}
        self.metrics: list[MetricRow] = []
        self.fail_downloads: set[str] = set()
        self.fail_creatives: set[str] = set()

    def add_creative(
        self,
        c: PlatformCreative,
        asset_bytes: bytes | None = None,
        child_assets: list[bytes] | None = None,
    ) -> None:
        self.creatives[c.platform_creative_id] = c
        if asset_bytes is not None and c.asset_url:
            self.assets[c.asset_url] = (asset_bytes, c.asset_mime or "application/octet-stream")
        if child_assets and c.child_asset_urls:
            for url, data in zip(c.child_asset_urls, child_assets, strict=False):
                self.assets[url] = (data, "image/jpeg")

    def add_ad(self, a: PlatformAd) -> None:
        self.ads.append(a)

    def add_metric(self, m: MetricRow) -> None:
        self.metrics.append(m)

    async def list_ads(self, account_id: str) -> list[PlatformAd]:
        return list(self.ads)

    async def get_creative(self, creative_id: str) -> PlatformCreative:
        if creative_id in self.fail_creatives:
            raise RuntimeError(f"get_creative failed: {creative_id}")
        return self.creatives[creative_id]

    async def download_asset(self, url: str) -> tuple[bytes, str]:
        if url in self.fail_downloads:
            raise RuntimeError(f"download failed: {url}")
        return self.assets[url]

    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]:
        return [m for m in self.metrics if since <= m.day <= until]
