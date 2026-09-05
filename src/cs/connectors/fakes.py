from __future__ import annotations

from datetime import date

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative


class FakeConnector:
    def __init__(self) -> None:
        self.ads: list[PlatformAd] = []
        self.creatives: dict[str, PlatformCreative] = {}
        self.assets: dict[str, tuple[bytes, str]] = {}
        self.metrics: list[MetricRow] = []
        self.uploads: list[tuple[str, str, int]] = []
        self.created_ads: list[dict] = []
        self.fail_downloads: set[str] = set()

    def add_creative(self, c: PlatformCreative, asset_bytes: bytes | None = None) -> None:
        self.creatives[c.platform_creative_id] = c
        if asset_bytes is not None and c.asset_url:
            self.assets[c.asset_url] = (asset_bytes, c.asset_mime or "application/octet-stream")

    def add_ad(self, a: PlatformAd) -> None:
        self.ads.append(a)

    def add_metric(self, m: MetricRow) -> None:
        self.metrics.append(m)

    async def list_ads(self, account_id: str) -> list[PlatformAd]:
        return list(self.ads)

    async def get_creative(self, creative_id: str) -> PlatformCreative:
        return self.creatives[creative_id]

    async def download_asset(self, url: str) -> tuple[bytes, str]:
        if url in self.fail_downloads:
            raise RuntimeError(f"download failed: {url}")
        return self.assets[url]

    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]:
        return [m for m in self.metrics if since <= m.day <= until]

    async def upload_image(self, account_id: str, data: bytes, name: str) -> str:
        self.uploads.append(("image", name, len(data)))
        return f"hash_{len(self.uploads)}"

    async def upload_video(self, account_id: str, data: bytes, name: str) -> str:
        self.uploads.append(("video", name, len(data)))
        return f"vid_{len(self.uploads)}"

    async def create_ad(self, account_id, adset_id, name, creative_spec, status) -> tuple[str, str]:
        n = len(self.created_ads) + 1
        self.created_ads.append(
            {
                "account_id": account_id,
                "adset_id": adset_id,
                "name": name,
                "creative_spec": creative_spec,
                "status": status,
            }
        )
        return f"cr_new_{n}", f"ad_new_{n}"
