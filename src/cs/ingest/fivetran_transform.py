from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import queue
from cs.ingest import repo
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.ingest")


def _q(schema: str, table: str) -> str:
    # schema is always schema_for(org_id)'s output -- [a-z0-9_]+ only -- so this is not an
    # injection risk; it's still double-quoted because a schema starting with a digit
    # (org_id is a UUID) is not a bare-word-safe Postgres identifier.
    return f'"{schema}".{table}'


def _carousel_urls(raw: str | None) -> list[str] | None:
    """object_story_link_data_child_attachments -> picture URLs, same shape parse.py's
    _carousel_card_urls expected from the live Graph API's link_data.child_attachments."""
    if not raw:
        return None
    try:
        cards = json.loads(raw)
        if not isinstance(cards, list):
            return None  # malformed/unexpected JSON shape -- not a list of cards
        urls = [c["picture"] for c in cards if isinstance(c, dict) and c.get("picture")]
    except (TypeError, ValueError):
        return None
    return urls or None


def _pid(value: object) -> str | None:
    """Fivetran lands Meta's numeric IDs (account_id, id, campaign_id, ad_set_id,
    creative_id, ...) as bigint -- confirmed against a real connector, not assumed --
    but every platform-id field downstream (ad_account.platform_account_id,
    ad.platform_ad_id, PlatformAd/PlatformCreative, dict keys used to join ads to
    creatives/accounts) is text. asyncpg refuses to bind an int for a text column
    parameter, so every id read from a landed table must be stringified before use.
    None passes through unchanged -- campaign_id/ad_set_id can be genuinely absent on
    some ad types."""
    return None if value is None else str(value)


def _format_and_urls(row: asyncpg.Record) -> tuple[str, str | None, list[str] | None]:
    if row["video_id"]:
        return "video", None, None
    child_urls = _carousel_urls(row["object_story_link_data_child_attachments"])
    if child_urls:
        return "carousel", None, child_urls
    return "image", row["image_url"] or row["thumbnail_url"], None


async def _discover_accounts(db, org_id: UUID, connection_id: UUID, schema: str) -> dict[str, UUID]:
    """Every distinct account_id seen in campaign_history becomes (or already is) an
    ad_account row, starting 'paused' until a human activates it -- same default the old
    OAuth callback used."""
    rows = await db.fetch(f"SELECT DISTINCT account_id FROM {_q(schema, 'campaign_history')}")
    by_platform_id: dict[str, UUID] = {}
    for r in rows:
        pid = _pid(r["account_id"])
        if pid is None:
            continue
        aid = await repo.upsert_ad_account(db, org_id, pid, pid, "USD", connection_id)
        by_platform_id[pid] = aid
    return by_platform_id


async def _sync_ads_and_creatives(
    db, org_id: UUID, schema: str, accounts_by_platform_id: dict[str, UUID]
) -> dict[str, int]:
    creative_rows = await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, "creative_history")}
        ) t WHERE rn = 1
    """)
    creatives_by_platform_id: dict[str, asyncpg.Record] = {_pid(r["id"]): r for r in creative_rows}

    ad_rows = await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, "ad_history")}
        ) t WHERE rn = 1
    """)

    new_creatives = 0
    synced_ads = 0
    failed = 0
    for ad_row in ad_rows:
        try:
            acct_id = accounts_by_platform_id.get(_pid(ad_row["account_id"]))
            if acct_id is None:
                continue  # not discovered yet, or discovered but not activated by the user
            cr = creatives_by_platform_id.get(_pid(ad_row["creative_id"]))
            if cr is None:
                continue  # ad references a creative Fivetran hasn't landed yet
            fmt, asset_url, child_urls = _format_and_urls(cr)
            platform_creative = PlatformCreative(
                platform_creative_id=_pid(cr["id"]),
                format=fmt,
                headline=cr["title"],
                primary_text=cr["body"],
                description=None,
                cta_type=cr["call_to_action_type"],
                link_url=cr["object_story_link_data_link"],
                asset_url=asset_url,
                asset_mime="image/jpeg" if asset_url else None,
                child_asset_urls=child_urls,
                raw=dict(cr),
            )
            creative_id, created = await repo.upsert_creative(
                db, org_id, acct_id, platform_creative, raw_path=""
            )
            new_creatives += int(created)

            platform_ad = PlatformAd(
                platform_ad_id=_pid(ad_row["id"]),
                name=ad_row["name"] or "",
                status=ad_row["status"] or "",
                adset_id=_pid(ad_row["ad_set_id"]),
                campaign_id=_pid(ad_row["campaign_id"]),
                platform_creative_id=_pid(cr["id"]),
            )
            await repo.upsert_ad(db, org_id, acct_id, platform_ad, creative_id)
            synced_ads += 1

            kind = "original_video" if fmt == "video" else "original_image"
            if fmt == "carousel" and child_urls:
                if not await repo.creative_has_primary_asset(db, org_id, creative_id):
                    await queue.enqueue(
                        db,
                        org_id,
                        "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": child_urls},
                        dedup_key=str(creative_id),
                    )
            elif asset_url and not await repo.creative_has_asset(db, org_id, creative_id, kind):
                await queue.enqueue(
                    db,
                    org_id,
                    "meta.download_creative",
                    {"creative_id": str(creative_id), "asset_url": asset_url, "kind": kind},
                    dedup_key=str(creative_id),
                )
            elif fmt == "video" and cr.get("video_id") is not None:
                # video_id alone doesn't give us a downloadable URL from these tables --
                # ad_video_history carries the source URL. Left for the insights/video
                # follow-up task rather than blocking entity sync on it.
                pass
        except Exception:
            # One malformed row (bad JSON, unexpected null, etc.) must not abort the
            # whole org's sync -- log it and keep going, same fault isolation the old
            # service.sync_account's per-creative try/except gave us.
            failed += 1
            log.exception(
                "failed to sync ad row",
                extra={"org_id": str(org_id), "platform_ad_id": ad_row["id"]},
            )

    return {"new_creatives": new_creatives, "synced_ads": synced_ads, "failed": failed}


async def discover_and_sync_entities(
    ctx: JobContext, org_id: UUID, connection: asyncpg.Record
) -> dict[str, Any]:
    schema = connection["schema_name"]
    accounts_by_platform_id = await _discover_accounts(ctx.db, org_id, connection["id"], schema)
    # Discovery lists every account Fivetran can see (so the user has something to pick
    # from in Settings), but ingestion -- ads, creatives, asset downloads, tagging -- only
    # ever runs for accounts the user has explicitly activated. A still-paused account
    # gets an ad_account row and nothing else: no creative content is ever pulled or
    # exposed until the user consents to it in our own UI, independent of whatever Meta/
    # Fivetran-level account scope the connect flow granted.
    active_ids = await repo.list_active_ad_account_ids(ctx.db, org_id)
    active_by_platform_id = {
        pid: aid for pid, aid in accounts_by_platform_id.items() if aid in active_ids
    }
    counts = await _sync_ads_and_creatives(ctx.db, org_id, schema, active_by_platform_id)
    return {"accounts_discovered": len(accounts_by_platform_id), **counts}
