from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

import asyncpg

from cs.connectors.base import PlatformAd, PlatformCreative
from cs.core import queue, repo
from cs.ingest.fivetran.classify import classify, stored_verdict
from cs.ingest.fivetran.schema import (
    AD_ASSET_CUSTOMIZATION_RULE,
    AD_ASSET_FEED_SPEC_IMAGE,
    AD_ASSET_FEED_SPEC_VIDEO,
    AD_HISTORY,
    AD_IMAGE_HISTORY,
    AD_VIDEO_HISTORY,
    BASIC_AD,
    BASIC_AD_ACTIONS,
    CAMPAIGN_HISTORY,
    CREATIVE_HISTORY,
    VIDEO_FULL_WATCH_TABLE,
    VIDEO_RETENTION_AD,
)
from cs.ingest.posters import meta_thumbnails
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.ingest")


def _q(schema: str, table: str) -> str:
    # schema is always schema_for(org_id)'s output -- [a-z0-9_]+ only -- so this is not an
    # injection risk; it's still double-quoted because a schema starting with a digit
    # (org_id is a UUID) is not a bare-word-safe Postgres identifier.
    return f'"{schema}".{table}'


async def _latest_rows(db, schema: str, table: str) -> list[asyncpg.Record]:
    """Fivetran's *_history tables carry one row per sync per id (a change log, not a
    snapshot) -- this collapses to the most recently synced row per id, the same pattern
    three separate call sites used to inline."""
    return await db.fetch(f"""
        SELECT * FROM (
          SELECT *, row_number() OVER (PARTITION BY id ORDER BY _fivetran_synced DESC) rn
          FROM {_q(schema, table)}
        ) t WHERE rn = 1
    """)


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


async def _optional_creative_ids(db, sql: str, *, table: str) -> set[str]:
    """A table Fivetran has not synced for this connection degrades completeness,
    never soundness -- the classifier falls back to 'unresolved', not 'image'. The
    warning is the precondition alert: coverage is silently reduced otherwise."""
    try:
        rows = await db.fetch(sql)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        # Deliberately narrow: this catch exists for a table Fivetran has not landed
        # yet, not for query failures in general. asyncpg.PostgresError also covers
        # ConnectionDoesNotExistError, QueryCanceledError (statement timeout -- and
        # _video_view_creative_ids below is an unbounded join over the whole of
        # basic_ad_actions, so a timeout here is plausible), SerializationError and
        # AdminShutdownError. Swallowing those would silently downgrade every
        # creative whose only evidence was this signal from 'video' to 'unresolved'
        # for the whole sync, org-wide -- soundness over completeness cuts the other
        # way for a failed query, which proves nothing about the data.
        log.warning(
            "classification signal unavailable -- coverage degraded, more creatives "
            "will be unresolved",
            extra={"table": table},
        )
        return set()
    return {str(r["creative_id"]) for r in rows if r["creative_id"] is not None}


async def _afs_video_creative_ids(db, schema: str) -> set[str]:
    return await _optional_creative_ids(
        db,
        f"SELECT DISTINCT creative_id FROM {_q(schema, AD_ASSET_FEED_SPEC_VIDEO)}"
        " WHERE video_id IS NOT NULL",
        table=AD_ASSET_FEED_SPEC_VIDEO,
    )


async def _video_view_creative_ids(db, schema: str) -> set[str]:
    return await _optional_creative_ids(
        db,
        f"""
        SELECT DISTINCT a.creative_id FROM (
          SELECT id, creative_id, row_number() OVER (PARTITION BY id
            ORDER BY _fivetran_synced DESC) rn FROM {_q(schema, AD_HISTORY)}
        ) a JOIN {_q(schema, BASIC_AD_ACTIONS)} act ON act.ad_id::text = a.id::text
        WHERE a.rn = 1 AND act.action_type = 'video_view' AND act.value > 0
        """,
        table=BASIC_AD_ACTIONS,
    )


async def _impressions_by_creative(db, schema: str) -> dict[str, int] | None:
    """Impressions per creative; None if basic_ad_actions is missing/empty, so views are unknown."""
    try:
        if await db.fetchval(f"SELECT 1 FROM {_q(schema, BASIC_AD_ACTIONS)} LIMIT 1") is None:
            log.warning("image_url rule disabled: no action data", extra={"schema": schema})
            return None
        rows = await db.fetch(f"""
            SELECT a.creative_id, sum(b.impressions) AS impressions FROM (
              SELECT id, creative_id, row_number() OVER (PARTITION BY id
                ORDER BY _fivetran_synced DESC) rn FROM {_q(schema, AD_HISTORY)}
            ) a JOIN {_q(schema, BASIC_AD)} b ON b.ad_id::text = a.id::text
            WHERE a.rn = 1 GROUP BY a.creative_id
        """)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        log.warning("image_url rule disabled: insights table missing", extra={"schema": schema})
        return None
    return {
        str(r["creative_id"]): int(r["impressions"] or 0)
        for r in rows
        if r["creative_id"] is not None
    }


def _json(raw: object) -> object:
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _label_ids(raw: object) -> set[str]:
    """adlabels (a list) or image_label (one object) -> the label ids it names."""
    parsed = _json(raw)
    items = parsed if isinstance(parsed, list) else [parsed]
    return {str(i["id"]) for i in items if isinstance(i, dict) and i.get("id") is not None}


async def _feed_spec_image_urls(db, schema: str) -> dict[str, str | None]:
    """Creatives with feed-spec images -> the full-size library URL of their feed image.

    Prefers the image a customization rule places in Facebook feed, then the first image
    whose hash is in ad_image_history. None when no hash resolves: still image evidence,
    just no file. A table that hasn't synced degrades to {} (unresolved, never image)."""
    try:
        images = await db.fetch(
            f"SELECT creative_id, index, hash, adlabels FROM {_q(schema, AD_ASSET_FEED_SPEC_IMAGE)}"
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        log.warning(
            "classification signal unavailable -- coverage degraded, more creatives "
            "will be unresolved",
            extra={"table": AD_ASSET_FEED_SPEC_IMAGE},
        )
        return {}
    try:
        library = {
            r["hash"]: r["url"]
            for r in await db.fetch(f"""
                SELECT hash, url FROM (
                  SELECT hash, url, row_number() OVER (PARTITION BY hash
                    ORDER BY _fivetran_synced DESC) rn FROM {_q(schema, AD_IMAGE_HISTORY)}
                ) t WHERE rn = 1 AND url IS NOT NULL
            """)
        }
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        log.warning("no full-size image files: image library missing", extra={"schema": schema})
        library = {}
    feed_labels = await _feed_placement_labels(db, schema, "image_label")

    by_creative: dict[str, list[asyncpg.Record]] = {}
    for r in images:
        if r["creative_id"] is not None:
            by_creative.setdefault(str(r["creative_id"]), []).append(r)
    out: dict[str, str | None] = {}
    for creative_id, rows in by_creative.items():
        rows.sort(key=lambda r: r["index"] if r["index"] is not None else 0)
        feed = feed_labels.get(creative_id, set())
        ordered = [r for r in rows if _label_ids(r["adlabels"]) & feed] + rows
        out[creative_id] = next((library[r["hash"]] for r in ordered if r["hash"] in library), None)
    return out


async def _feed_placement_labels(db, schema: str, label_column: str) -> dict[str, set[str]]:
    """creative_id -> label ids that a customization rule places in Facebook feed."""
    try:
        rules = await db.fetch(
            f"SELECT creative_id, {label_column} AS label, customization_spec"
            f" FROM {_q(schema, AD_ASSET_CUSTOMIZATION_RULE)} WHERE {label_column} IS NOT NULL"
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return {}
    out: dict[str, set[str]] = {}
    for r in rules:
        spec = _json(r["customization_spec"])
        if isinstance(spec, dict) and "feed" in (spec.get("facebook_positions") or []):
            out.setdefault(str(r["creative_id"]), set()).update(_label_ids(r["label"]))
    return out


async def _feed_spec_video_ids(db, schema: str) -> dict[str, list[str]]:
    """creative_id -> its feed-spec video ids, the Facebook feed placement's video first."""
    try:
        rows = await db.fetch(
            "SELECT creative_id, video_id, index, adlabels"
            f" FROM {_q(schema, AD_ASSET_FEED_SPEC_VIDEO)}"
            " WHERE video_id IS NOT NULL ORDER BY creative_id, index"
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        return {}
    feed = await _feed_placement_labels(db, schema, "video_label")
    out: dict[str, list[str]] = {}
    for r in rows:
        cid, vid = str(r["creative_id"]), str(r["video_id"])
        ids = out.setdefault(cid, [])
        if vid in ids:
            continue
        if _label_ids(r["adlabels"]) & feed.get(cid, set()):
            ids.insert(0, vid)
        else:
            ids.append(vid)
    return out


def _length_payload(cr: asyncpg.Record, video_length_ms: dict[str, int]) -> dict:
    """Meta's own duration for this creative's video, when the library knows it."""
    vid = _pid(cr["video_video_id"] or cr["video_id"])
    ms = video_length_ms.get(vid or "")
    return {"duration_ms": ms} if ms else {}


def _video_ids_to_resolve(cr: asyncpg.Record, feed_spec_videos: dict[str, list[str]]) -> list[str]:
    own = _pid(cr["video_video_id"] or cr["video_id"])
    return feed_spec_videos.get(_pid(cr["id"]) or "") or ([own] if own else [])


async def _full_watch_creative_ids(db, schema: str) -> set[str] | None:
    """Creatives with at least one ad watched to 100%; None when the retention report is
    missing or empty, since then an unwatched image_url creative proves nothing."""
    try:
        if await db.fetchval(f"SELECT 1 FROM {_q(schema, VIDEO_RETENTION_AD)} LIMIT 1") is None:
            log.warning("image_url rule disabled: no retention data", extra={"schema": schema})
            return None
        rows = await db.fetch(f"""
            SELECT DISTINCT a.creative_id FROM (
              SELECT id, creative_id, row_number() OVER (PARTITION BY id
                ORDER BY _fivetran_synced DESC) rn FROM {_q(schema, AD_HISTORY)}
            ) a JOIN {_q(schema, VIDEO_FULL_WATCH_TABLE)} w ON w.ad_id::text = a.id::text
            WHERE a.rn = 1 AND w.action_type = 'video_view' AND w.value > 0
        """)
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        log.warning("image_url rule disabled: retention report missing", extra={"schema": schema})
        return None
    return {str(r["creative_id"]) for r in rows if r["creative_id"] is not None}


async def _campaign_names(db, schema: str) -> dict[str, str]:
    """Each campaign's name as of its latest synced row."""
    return {
        pid: r["name"]
        for r in await _latest_rows(db, schema, CAMPAIGN_HISTORY)
        if r["name"] and (pid := _pid(r["id"]))
    }


async def _adset_names(db, schema: str, ad_rows: list[asyncpg.Record]) -> dict[str, str]:
    """Each ad set's name from basic_ad, which carries adset_name on every row; adset_history
    is not synced. Keyed by ad set through the ads' own ad_set_id, so an ad that never
    delivered still gets its ad set's name from a sibling that did. The latest day wins, so a
    renamed ad set shows its current name. Display only: a missing table or column gives
    no names, never a failed sync."""
    try:
        rows = await db.fetch(
            f"SELECT DISTINCT ON (ad_id) ad_id, adset_name, date FROM {_q(schema, BASIC_AD)} "
            "WHERE adset_name IS NOT NULL ORDER BY ad_id, date DESC"
        )
    except (asyncpg.UndefinedTableError, asyncpg.UndefinedColumnError):
        log.warning("ad set names unavailable", extra={"table": BASIC_AD})
        return {}
    adset_of = {_pid(r["id"]): _pid(r["ad_set_id"]) for r in ad_rows}
    latest: dict[str, tuple] = {}
    for r in rows:
        adset = adset_of.get(_pid(r["ad_id"]))
        if adset and (adset not in latest or r["date"] > latest[adset][0]):
            latest[adset] = (r["date"], r["adset_name"])
    return {k: name for k, (_, name) in latest.items()}


async def _discover_accounts(db, org_id: UUID, connection_id: UUID, schema: str) -> dict[str, UUID]:
    """Every distinct account_id seen in campaign_history becomes (or already is) an
    ad_account row, starting 'paused' until a human activates it -- same default the old
    OAuth callback used."""
    rows = await db.fetch(f"SELECT DISTINCT account_id FROM {_q(schema, CAMPAIGN_HISTORY)}")
    by_platform_id: dict[str, UUID] = {}
    for r in rows:
        pid = _pid(r["account_id"])
        if pid is None:
            continue
        aid = await repo.upsert_ad_account(db, org_id, pid, pid, "USD", connection_id)
        by_platform_id[pid] = aid
    return by_platform_id


async def _sync_ads_and_creatives(
    db,
    org_id: UUID,
    schema: str,
    accounts_by_platform_id: dict[str, UUID],
    apify_limit: int,
) -> dict[str, int]:
    creative_rows = await _latest_rows(db, schema, CREATIVE_HISTORY)
    creatives_by_platform_id: dict[str, asyncpg.Record] = {_pid(r["id"]): r for r in creative_rows}

    ad_rows = await _latest_rows(db, schema, AD_HISTORY)
    campaign_names = await _campaign_names(db, schema)
    adset_names = await _adset_names(db, schema, ad_rows)

    video_rows = await _latest_rows(db, schema, AD_VIDEO_HISTORY)
    videos_by_id: dict[str, str] = {
        pid: r["source"] for r in video_rows if r["source"] and (pid := _pid(r["id"]))
    }
    # Meta reports each video's length in seconds; it is authoritative and free, so the
    # download stores it instead of running ffprobe (which stays the fallback for videos
    # Apify fetches from outside the ad account's library).
    video_length_ms: dict[str, int] = {
        pid: int(float(length) * 1000)
        for r in video_rows
        if (length := (r["length"] if "length" in r.keys() else None)) is not None
        and (pid := _pid(r["id"]))
    }
    afs_ids = await _afs_video_creative_ids(db, schema)
    vv_ids = await _video_view_creative_ids(db, schema)
    impressions = await _impressions_by_creative(db, schema)
    feed_spec_images = await _feed_spec_image_urls(db, schema)
    full_watches = await _full_watch_creative_ids(db, schema)
    feed_spec_videos = await _feed_spec_video_ids(db, schema)

    new_creatives = 0
    synced_ads = 0
    failed = 0
    apify_queued = apify_deferred = 0
    for ad_row in ad_rows:
        try:
            acct_id = accounts_by_platform_id.get(_pid(ad_row["account_id"]))
            if acct_id is None:
                continue  # not discovered yet, or discovered but not activated by the user
            cr = creatives_by_platform_id.get(_pid(ad_row["creative_id"]))
            if cr is None:
                continue  # ad references a creative Fivetran hasn't landed yet
            verdict = classify(
                cr,
                videos_by_id=videos_by_id,
                afs_video_creative_ids=afs_ids,
                video_view_creative_ids=vv_ids,
                impressions_by_creative_id=impressions,
                feed_spec_image_urls=feed_spec_images,
                full_watch_creative_ids=full_watches,
            )
            # image/video are only stored once the file is; until then, unresolved.
            stored_format, stored_source = stored_verdict(
                verdict, await repo.primary_asset_kind(db, acct_id, _pid(cr["id"]))
            )
            platform_creative = PlatformCreative(
                platform_creative_id=_pid(cr["id"]),
                format=stored_format,
                headline=cr["title"],
                primary_text=cr["body"],
                description=None,
                cta_type=cr["call_to_action_type"],
                link_url=cr["object_story_link_data_link"],
                asset_url=verdict.asset_url,
                asset_mime=None,
                child_asset_urls=verdict.child_urls,
                raw=dict(cr),
            )
            creative_id, created = await repo.upsert_creative(
                db,
                org_id,
                acct_id,
                platform_creative,
                raw_path="",
                format_source=stored_source,
            )
            new_creatives += int(created)

            platform_ad = PlatformAd(
                platform_ad_id=_pid(ad_row["id"]),
                name=ad_row["name"] or "",
                status=ad_row["status"] or "",
                adset_id=_pid(ad_row["ad_set_id"]),
                campaign_id=_pid(ad_row["campaign_id"]),
                platform_creative_id=_pid(cr["id"]),
                campaign_name=campaign_names.get(_pid(ad_row["campaign_id"])),
                adset_name=adset_names.get(_pid(ad_row["ad_set_id"])),
                effective_status=ad_row.get("effective_status"),
            )
            await repo.upsert_ad(db, org_id, acct_id, platform_ad, creative_id)
            synced_ads += 1

            if verdict.format == "unresolved":
                continue  # nothing proves a format; download and tagging stay blocked
            kind = "original_video" if verdict.format == "video" else "original_image"
            if verdict.format == "carousel" and verdict.child_urls:
                if not await repo.creative_has_primary_asset(db, org_id, creative_id):
                    await queue.enqueue(
                        db,
                        org_id,
                        "meta.download_carousel",
                        {"creative_id": str(creative_id), "asset_urls": verdict.child_urls},
                        dedup_key=str(creative_id),
                    )
            elif verdict.asset_url and not await repo.creative_has_asset(
                db, org_id, creative_id, kind
            ):
                await queue.enqueue(
                    db,
                    org_id,
                    "meta.download_creative",
                    {
                        "creative_id": str(creative_id),
                        "asset_url": verdict.asset_url,
                        "kind": kind,
                        "format": verdict.format,
                        "format_source": verdict.source,
                        **_length_payload(cr, video_length_ms),
                    },
                    dedup_key=str(creative_id),
                )
            elif (
                verdict.format == "video"
                and not verdict.asset_url
                and cr["instagram_permalink_url"]
                and not await repo.creative_has_asset(db, org_id, creative_id, kind)
                and not await repo.has_video_resolution(db, creative_id)
            ):
                if apify_queued >= apify_limit:
                    apify_deferred += 1
                    continue
                # video_video_id didn't resolve against ad_video_history -- this is a
                # Partnership Ad whose video lives in the creator's own Instagram account,
                # not the ad account's video library. Apify can still fetch it from the
                # permalink. The video_resolution ledger allows one paid lookup per creative,
                # so a creative that was ever looked up is never queued again.
                apify_queued += 1
                await queue.enqueue(
                    db,
                    org_id,
                    "meta.resolve_instagram_video",
                    {
                        "creative_id": str(creative_id),
                        "permalink_url": cr["instagram_permalink_url"],
                        "kind": kind,
                        "format": verdict.format,
                        "format_source": verdict.source,
                    },
                    dedup_key=str(creative_id),
                )
            elif (
                verdict.format == "video"
                and not verdict.asset_url
                and not cr["instagram_permalink_url"]
                and (video_ids := _video_ids_to_resolve(cr, feed_spec_videos))
                and not await repo.creative_has_asset(db, org_id, creative_id, kind)
                and not await repo.has_video_resolution(db, creative_id)
            ):
                if apify_queued >= apify_limit:
                    apify_deferred += 1
                    continue
                # No synced source and no Instagram post: Facebook's public player serves
                # the video if it was published on the Page. One Apify run tries every id.
                apify_queued += 1
                await queue.enqueue(
                    db,
                    org_id,
                    "meta.resolve_facebook_video",
                    {
                        "creative_id": str(creative_id),
                        "video_ids": video_ids,
                        "kind": kind,
                        "format": verdict.format,
                        "format_source": verdict.source,
                    },
                    dedup_key=str(creative_id),
                )
        except Exception:
            # One malformed row (bad JSON, unexpected null, etc.) must not abort the
            # whole org's sync -- log it and keep going, same fault isolation the old
            # service.sync_account's per-creative try/except gave us.
            failed += 1
            log.exception(
                "failed to sync ad row",
                extra={"org_id": str(org_id), "platform_ad_id": ad_row["id"]},
            )

    if apify_deferred:
        log.warning(
            "apify lookups over the per-sync limit wait for a later sync",
            extra={"org_id": str(org_id), "queued": apify_queued, "deferred": apify_deferred},
        )
    return {
        "new_creatives": new_creatives,
        "synced_ads": synced_ads,
        "failed": failed,
        "apify_queued": apify_queued,
        "apify_deferred": apify_deferred,
    }


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
    counts = await _sync_ads_and_creatives(
        ctx.db, org_id, schema, active_by_platform_id, ctx.settings.apify_lookups_per_sync
    )
    # Display only, so nothing about the sync depends on it.
    try:
        counts["meta_thumbnails"] = await meta_thumbnails(ctx.db, ctx.blobs, org_id)
    except Exception as exc:
        log.warning("Meta thumbnails failed for org %s: %s", org_id, exc)
    return {"accounts_discovered": len(accounts_by_platform_id), **counts}
