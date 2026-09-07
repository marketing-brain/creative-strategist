from __future__ import annotations

import json
import logging
from datetime import date

from cs.connectors.base import MetricRow, PlatformAd, PlatformCreative
from cs.connectors.meta import parse
from cs.connectors.meta.client import GraphClient

log = logging.getLogger("cs.meta")

AD_FIELDS = "id,name,status,adset_id,campaign_id,creative{id}"
CREATIVE_FIELDS = (
    "id,name,body,title,call_to_action_type,link_url,image_url,image_hash,video_id,"
    "thumbnail_url,object_story_spec,asset_feed_spec"
)
INSIGHT_FIELDS = (
    "ad_id,date_start,date_stop,spend,impressions,clicks,inline_link_clicks,actions,"
    "action_values,video_play_actions,video_thruplay_watched_actions,"
    "video_p50_watched_actions,video_p100_watched_actions"
)


class MetaConnector:
    def __init__(self, client: GraphClient):
        self.client = client

    async def list_ads(self, account_id: str) -> list[PlatformAd]:
        rows = await self.client.get_all(
            f"act_{account_id}/ads", {"fields": AD_FIELDS, "limit": 200}
        )
        ads = [
            PlatformAd(
                str(r["id"]),
                r.get("name", ""),
                r.get("status", ""),
                r.get("adset_id"),
                r.get("campaign_id"),
                str(r["creative"]["id"]),
            )
            for r in rows
            if r.get("creative")
        ]
        dropped = len(rows) - len(ads)
        if dropped:
            log.warning(
                "meta ads dropped without creative",
                extra={"account_id": account_id, "dropped": dropped},
            )
        return ads

    async def get_creative(self, creative_id: str) -> PlatformCreative:
        obj = await self.client.get(creative_id, {"fields": CREATIVE_FIELDS})
        c = parse.creative_from_graph(obj)
        if c.format == "video" and c.raw.get("video_id"):
            v = await self.client.get(str(c.raw["video_id"]), {"fields": "source,length"})
            c.asset_url, c.asset_mime = v.get("source"), "video/mp4"
        return c

    async def download_asset(self, url: str) -> tuple[bytes, str]:
        resp = await self.client.http.get(url, follow_redirects=True, timeout=120)
        resp.raise_for_status()
        return resp.content, resp.headers.get("content-type", "application/octet-stream").split(
            ";"
        )[0]

    async def fetch_metrics(self, account_id: str, since: date, until: date) -> list[MetricRow]:
        rows = await self.client.get_all(
            f"act_{account_id}/insights",
            {
                "level": "ad",
                "time_increment": 1,
                "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
                "breakdowns": "publisher_platform,platform_position",
                "fields": INSIGHT_FIELDS,
                "limit": 500,
            },
        )
        return [parse.metric_from_insight(r) for r in rows]
