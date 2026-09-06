from __future__ import annotations

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import asyncpg
import httpx

from cs.connectors.base import AdPlatformConnector, PlatformCreative
from cs.connectors.meta.client import GraphClient
from cs.connectors.meta.connector import MetaConnector
from cs.connectors.meta.scrub import strip_access_tokens
from cs.core import queue
from cs.core.blobs import BlobStore, sha256_hex
from cs.core.config import Settings
from cs.core.crypto import decrypt_str
from cs.ingest import repo
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.ingest")

BACKFILL_DAYS = 180
INCREMENTAL_DAYS = 3


def _raw_sink(blobs: BlobStore, org_id: UUID, account: str):
    n = 0
    ts = int(time.time() * 1000)

    async def sink(endpoint: str, page: dict) -> None:
        nonlocal n
        n += 1
        key = f"{org_id}/raw/meta/{account}/{endpoint.replace('/', '_')}/{ts}-{n}.json"
        # Raw blobs are org-readable; Meta echoes the access token back in paging URLs.
        body = json.dumps(strip_access_tokens(page)).encode()
        await blobs.put(key, body, "application/json")

    return sink


def default_connector_factory(
    settings: Settings, account: asyncpg.Record, http: httpx.AsyncClient, blobs: BlobStore
) -> AdPlatformConnector:
    token = decrypt_str(settings, account["access_token_enc"])
    sink = _raw_sink(blobs, account["org_id"], account["platform_account_id"])
    return MetaConnector(GraphClient(settings, token, http, raw_sink=sink))


CONNECTOR_FACTORY = default_connector_factory


async def sync_account(
    ctx: JobContext,
    account: asyncpg.Record,
    connector: AdPlatformConnector,
    since: date,
    until: date,
) -> dict:
    org_id, acct_id = account["org_id"], account["id"]
    ads = await connector.list_ads(account["platform_account_id"])
    new_creatives = 0
    failed_creatives = 0
    # Many ads share one creative; fetch and store each distinct creative once per sync.
    seen: dict[str, PlatformCreative] = {}
    for ad in ads:
        try:
            c = seen.get(ad.platform_creative_id)
            first_fetch = c is None
            if c is None:
                c = await connector.get_creative(ad.platform_creative_id)
                seen[ad.platform_creative_id] = c
            raw_key = (
                f"{org_id}/raw/meta/{account['platform_account_id']}/creative/"
                f"{c.platform_creative_id}.json"
            )
            if first_fetch:
                await ctx.blobs.put(
                    raw_key, json.dumps(c.raw, default=str).encode(), "application/json"
                )
            creative_id, created = await repo.upsert_creative(ctx.db, org_id, acct_id, c, raw_key)
            new_creatives += int(created)
            if created:
                ga = await ctx.db.fetchval(
                    "SELECT generated_asset_id FROM publish_job WHERE org_id=$1 "
                    "AND platform_creative_id=$2 AND status='done' LIMIT 1",
                    org_id,
                    c.platform_creative_id,
                )
                if ga:
                    await ctx.db.execute(
                        "UPDATE creative SET generated_asset_id=$2 WHERE id=$1", creative_id, ga
                    )
            await repo.upsert_ad(ctx.db, org_id, acct_id, ad, creative_id)
            kind = "original_video" if c.format == "video" else "original_image"
            if c.format == "carousel" and c.child_asset_urls:
                if not await repo.creative_has_primary_asset(ctx.db, org_id, creative_id):
                    await queue.enqueue(
                        ctx.db,
                        org_id,
                        "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": c.child_asset_urls},
                        dedup_key=str(creative_id),
                    )
            elif c.asset_url and not await repo.creative_has_asset(
                ctx.db, org_id, creative_id, kind
            ):
                await queue.enqueue(
                    ctx.db,
                    org_id,
                    "meta.download_creative",
                    {"creative_id": str(creative_id), "asset_url": c.asset_url, "kind": kind},
                    dedup_key=str(creative_id),
                )
        except Exception:  # noqa: BLE001 - one bad creative must not abort the account sync
            failed_creatives += 1
            log.exception(
                "creative sync failed",
                extra={
                    "org_id": str(org_id),
                    "ad_account_id": str(acct_id),
                    "platform_creative_id": ad.platform_creative_id,
                },
            )
    rows = await connector.fetch_metrics(account["platform_account_id"], since, until)
    n_metrics, n_skipped = await repo.upsert_metrics(ctx.db, org_id, acct_id, rows)
    cursor = dict(account["sync_cursor"])
    cursor.update(
        {
            "schema_version": 1,
            "last_synced_until": until.isoformat(),
            "last_synced_at": datetime.now(UTC).isoformat(),
        }
    )
    await ctx.db.execute("UPDATE ad_account SET sync_cursor=$2 WHERE id=$1", acct_id, cursor)
    return {
        "ads": len(ads),
        "new_creatives": new_creatives,
        "metrics": n_metrics,
        "metrics_skipped": n_skipped,
        "failed_creatives": failed_creatives,
    }


async def download_creative(
    ctx: JobContext, connector: AdPlatformConnector, creative_id: UUID, asset_url: str, kind: str
) -> None:
    org_id = ctx.job.org_id
    data, mime = await connector.download_asset(asset_url)
    ext = {"image/png": "png", "image/jpeg": "jpg", "video/mp4": "mp4"}.get(mime, "bin")
    key = f"{org_id}/creatives/{creative_id}/original.{ext}"
    await ctx.blobs.put(key, data, mime)
    asset_id = await repo.insert_asset(
        ctx.db, org_id, creative_id, kind, key, sha256_hex(data), mime
    )
    await repo.set_primary_asset(ctx.db, org_id, creative_id, asset_id)
    await queue.enqueue(
        ctx.db,
        org_id,
        "tagging.tag_creative",
        {"creative_id": str(creative_id)},
        dedup_key=f"tag:{creative_id}",
    )


async def download_carousel(
    ctx: JobContext, connector: AdPlatformConnector, creative_id: UUID, asset_urls: list[str]
) -> None:
    org_id = ctx.job.org_id
    primary_id = None
    for i, url in enumerate(asset_urls):
        existing = await repo.get_asset_by_seq(ctx.db, org_id, creative_id, "original_image", i)
        if existing is not None:
            asset_id = existing["id"]
        else:
            data, mime = await connector.download_asset(url)
            ext = {"image/png": "png", "image/jpeg": "jpg"}.get(mime, "jpg")
            key = f"{org_id}/creatives/{creative_id}/card_{i:02d}.{ext}"
            await ctx.blobs.put(key, data, mime)
            asset_id = await repo.insert_asset(
                ctx.db, org_id, creative_id, "original_image", key, sha256_hex(data), mime, seq=i
            )
        if i == 0:
            primary_id = asset_id
    if primary_id is not None:
        await repo.set_primary_asset(ctx.db, org_id, creative_id, primary_id)
        await queue.enqueue(
            ctx.db,
            org_id,
            "tagging.tag_creative",
            {"creative_id": str(creative_id)},
            dedup_key=f"tag:{creative_id}",
        )


def window(days: int, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    return today - timedelta(days=days), today
