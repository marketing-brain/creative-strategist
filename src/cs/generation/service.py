from __future__ import annotations

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
        i = await db.fetchrow("SELECT * FROM insight WHERE org_id=$1 AND id=$2", org_id, g["source_insight_id"])
        if i:
            lines += [f"Winning pattern: {i['dimension']} = {i['value']} ({i['metric'].upper()} lift {i['lift'] * 100:+.0f}%).",
                      f"Why: {i['explanation']}", f"Direction: {i['suggested_test']}"]
    if g["concept"]:
        lines.append(f"Concept: {g['concept']}")
    for cid in g["source_creative_ids"] or []:
        c = await db.fetchrow("SELECT headline, primary_text FROM creative WHERE org_id=$1 AND id=$2", org_id, cid)
        if c:
            tags = await tag_repo.latest_tags(db, org_id, cid)
            top = ", ".join(f"{t['dimension']}={t['value']}" for t in tags[:8])
            lines.append(f"Reference creative: headline '{c['headline']}', text '{c['primary_text']}', tags: {top}")
    return "\n".join(lines) or "Fresh concept for the brand."


def _image_prompt(brief: str, kit, copy) -> str:
    return (f"Paid social ad image. {brief}\nHeadline to visually support (do not render text): {copy.headline}. "
            f"Brand colors: {', '.join(kit.colors) or 'any'}. Clean composition, room for a logo in the bottom right, "
            f"photo-real, no watermark, no text.")


async def run_generation(ctx: JobContext, generation_id: UUID, copywriter: CopyWriter, image_gen: ImageGenerator,
                         video_gen: VideoGenerator) -> None:
    org_id = ctx.job.org_id
    g = await repo.get_generation(ctx.db, org_id, generation_id)
    kit = await get_brand_kit(ctx.db, org_id, g["brand_kit_id"])
    await repo.set_generation_status(ctx.db, org_id, generation_id, "running")
    try:
        brief = await build_brief(ctx.db, org_id, g)
        sets = await copywriter.write(brief, kit, g["variants"])
        if not sets:
            raise RuntimeError("no copy passed the brand check")
        refs = []
        for cid in (g["source_creative_ids"] or [])[:2]:
            c = await ctx.db.fetchrow(
                "SELECT a.path FROM creative c JOIN asset a ON a.id=c.primary_asset_id "
                "WHERE c.org_id=$1 AND c.id=$2 AND c.format='image'", org_id, cid)
            if c:
                refs.append(await ctx.blobs.get(c["path"]))
        logo = await ctx.blobs.get(kit.logo_path) if kit.logo_path else None
        prompt = _image_prompt(brief, kit, sets[0])
        raws = await image_gen.generate(prompt, refs, n=len(sets))
        await repo.set_generation_status(ctx.db, org_id, generation_id, "running", model=ctx.settings.image_model, prompt=prompt)
        produced = 0
        first_still_key: str | None = None
        for variant, (raw, copy) in enumerate(zip(raws, sets, strict=False), start=1):
            for aspect in ASPECTS:
                img = fit_to_aspect(raw, aspect)
                if logo:
                    img = overlay_logo(img, logo)
                key = f"{org_id}/generated/{generation_id}/v{variant}-{aspect.replace(':', 'x')}.jpg"
                await ctx.blobs.put(key, img, "image/jpeg")
                await repo.insert_generated_asset(ctx.db, org_id, generation_id, "image", aspect, variant, key, sha256_hex(img),
                                                  copy.headline, copy.primary_text, copy.cta_type)
                produced += 1
                if aspect == "9:16" and first_still_key is None:
                    first_still_key = key
        if g["kind"] == "video" and first_still_key:
            still_url = await ctx.blobs.url_for(first_still_key, expires_seconds=3600)
            clip = await video_gen.generate(still_url, f"Subtle cinematic motion. {sets[0].headline}", VIDEO_SECONDS, "9:16")
            final = video_with_end_card(clip, sets[0].headline, logo, kit.colors[0] if kit.colors else "#000000", "9:16")
            key = f"{org_id}/generated/{generation_id}/v1-9x16.mp4"
            await ctx.blobs.put(key, final, "video/mp4")
            await repo.insert_generated_asset(ctx.db, org_id, generation_id, "video", "9:16", 1, key, sha256_hex(final),
                                              sets[0].headline, sets[0].primary_text, sets[0].cta_type)
            produced += 1
        await repo.set_generation_status(ctx.db, org_id, generation_id, "done")
    except Exception as exc:
        await repo.set_generation_status(ctx.db, org_id, generation_id, "failed", error=str(exc)[:2000])
        raise
