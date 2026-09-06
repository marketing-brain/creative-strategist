from __future__ import annotations

import asyncio
from uuid import UUID

from cs.core.blobs import sha256_hex
from cs.core.db import Database
from cs.generation import repo
from cs.generation.brand import get_brand_kit
from cs.generation.compose import ASPECTS, fit_to_aspect, overlay_logo, video_with_end_card
from cs.generation.copy import CopyWriter
from cs.generation.images import ImageGenerator
from cs.generation.video import VideoGenerator
from cs.jobs.registry import JobContext
from cs.tagging import repo as tag_repo

VIDEO_SECONDS = 5


async def build_brief(db: Database, org_id: UUID, g) -> str:
    lines: list[str] = []
    if g["source_insight_id"]:
        i = await db.fetchrow(
            "SELECT * FROM insight WHERE org_id=$1 AND id=$2", org_id, g["source_insight_id"]
        )
        if i:
            lift_pct = i["lift"] * 100
            lines += [
                f"Winning pattern: {i['dimension']} = {i['value']} "
                f"({i['metric'].upper()} lift {lift_pct:+.0f}%).",
                f"Why: {i['explanation']}",
                f"Direction: {i['suggested_test']}",
            ]
    if g["concept"]:
        lines.append(f"Concept: {g['concept']}")
    for cid in g["source_creative_ids"] or []:
        c = await db.fetchrow(
            "SELECT headline, primary_text FROM creative WHERE org_id=$1 AND id=$2", org_id, cid
        )
        if c:
            tags = await tag_repo.latest_tags(db, org_id, cid)
            top = ", ".join(f"{t['dimension']}={t['value']}" for t in tags[:8])
            lines.append(
                f"Reference creative: headline '{c['headline']}', "
                f"text '{c['primary_text']}', tags: {top}"
            )
    return "\n".join(lines) or "Fresh concept for the brand."


def _image_prompt(brief: str, kit, copy) -> str:
    return (
        f"Paid social ad image. {brief}\n"
        f"Headline to visually support (do not render text): {copy.headline}. "
        f"Brand colors: {', '.join(kit.colors) or 'any'}. Clean composition, room for a logo "
        f"in the bottom right, photo-real, no watermark, no text."
    )


async def run_generation(
    ctx: JobContext,
    generation_id: UUID,
    copywriter: CopyWriter,
    image_gen: ImageGenerator,
    video_gen: VideoGenerator,
) -> None:
    org_id = ctx.job.org_id
    try:
        g = await repo.get_generation(ctx.db, org_id, generation_id)
        if g is None:
            raise RuntimeError("generation not found")
        kit = await get_brand_kit(ctx.db, org_id, g["brand_kit_id"])
        if kit is None:
            raise RuntimeError("brand kit not found")
        await repo.set_generation_status(ctx.db, org_id, generation_id, "running")
        # Idempotency: the job queue retries a failed attempt (see cs.core.queue.fail), and a
        # partial prior run may have already inserted generated_asset rows and paid for image
        # generation. Clear this generation's prior output before producing anything new so a
        # retry starts clean instead of compounding duplicates with each attempt -- mirrors
        # cs.tagging.runner._prepare_inputs's delete-before-insert pattern for the video branch.
        await repo.delete_generated_assets(ctx.db, org_id, generation_id)
        brief = await build_brief(ctx.db, org_id, g)
        sets = await copywriter.write(brief, kit, g["variants"])
        if not sets:
            raise RuntimeError("no copy passed the brand check")
        # A real copywriter response could return more sets than requested; clamp so that
        # doesn't silently inflate image-generation spend past the caller's own cap.
        sets = sets[: g["variants"]]
        refs = []
        for cid in (g["source_creative_ids"] or [])[:2]:
            c = await ctx.db.fetchrow(
                "SELECT a.path FROM creative c JOIN asset a ON a.id=c.primary_asset_id "
                "WHERE c.org_id=$1 AND c.id=$2 AND c.format='image'",
                org_id,
                cid,
            )
            if c:
                refs.append(await ctx.blobs.get(c["path"]))
        logo = await ctx.blobs.get(kit.logo_path) if kit.logo_path else None
        prompt = _image_prompt(brief, kit, sets[0])
        raws = await image_gen.generate(prompt, refs, n=len(sets))
        await repo.set_generation_status(
            ctx.db, org_id, generation_id, "running", model=ctx.settings.image_model, prompt=prompt
        )
        # delete_generated_assets only clears non-approved rows, so any row still occupying a
        # (kind, aspect, variant) slot survived because a human already approved it (possibly
        # already published). Fetch them all once rather than a query per slot, and leave both
        # the approved row and the blob it points at untouched -- never regenerate over it.
        existing_by_slot = {
            (a["kind"], a["aspect"], a["variant"]): a
            for a in await repo.list_generated_assets(ctx.db, org_id, generation_id=generation_id)
        }
        produced = 0
        first_still_key: str | None = None
        first_still_copy: tuple[str, str, str] | None = None
        for variant, (raw, copy) in enumerate(zip(raws, sets, strict=False), start=1):
            for aspect in ASPECTS:
                existing = existing_by_slot.get(("image", aspect, variant))
                if existing is not None:
                    if aspect == "9:16" and first_still_key is None:
                        first_still_key = existing["path"]
                        first_still_copy = (
                            existing["headline"],
                            existing["primary_text"],
                            existing["cta_type"],
                        )
                    continue
                img = await asyncio.to_thread(fit_to_aspect, raw, aspect)
                if logo:
                    img = await asyncio.to_thread(overlay_logo, img, logo)
                key = (
                    f"{org_id}/generated/{generation_id}/v{variant}-{aspect.replace(':', 'x')}.jpg"
                )
                await ctx.blobs.put(key, img, "image/jpeg")
                await repo.insert_generated_asset(
                    ctx.db,
                    org_id,
                    generation_id,
                    "image",
                    aspect,
                    variant,
                    key,
                    sha256_hex(img),
                    copy.headline,
                    copy.primary_text,
                    copy.cta_type,
                )
                produced += 1
                if aspect == "9:16" and first_still_key is None:
                    first_still_key = key
                    first_still_copy = (copy.headline, copy.primary_text, copy.cta_type)
        existing_video = (
            existing_by_slot.get(("video", "9:16", 1)) if g["kind"] == "video" else None
        )
        if g["kind"] == "video" and first_still_key and first_still_copy and existing_video is None:
            headline, primary_text, cta_type = first_still_copy
            still_url = await ctx.blobs.url_for(first_still_key, expires_seconds=3600)
            clip = await video_gen.generate(
                still_url, f"Subtle cinematic motion. {headline}", VIDEO_SECONDS, "9:16"
            )
            final = await asyncio.to_thread(
                video_with_end_card,
                clip,
                headline,
                logo,
                kit.colors[0] if kit.colors else "#000000",
                "9:16",
            )
            key = f"{org_id}/generated/{generation_id}/v1-9x16.mp4"
            await ctx.blobs.put(key, final, "video/mp4")
            await repo.insert_generated_asset(
                ctx.db,
                org_id,
                generation_id,
                "video",
                "9:16",
                1,
                key,
                sha256_hex(final),
                headline,
                primary_text,
                cta_type,
            )
            produced += 1
        await repo.set_generation_status(ctx.db, org_id, generation_id, "done")
    except Exception as exc:
        await repo.set_generation_status(
            ctx.db, org_id, generation_id, "failed", error=str(exc)[:2000]
        )
        raise
