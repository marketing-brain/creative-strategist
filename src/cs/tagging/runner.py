from __future__ import annotations

from uuid import UUID

from cs.core import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.tagging import repo
from cs.tagging.images import prepare_image
from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import Tagger
from cs.tagging.taxonomy import load_taxonomy


async def load_media(
    ctx: JobContext, creative
) -> tuple[list[tuple[bytes, str]], tuple[bytes, str] | None]:
    """The creative's media as model input: card JPEGs for carousels, one JPEG for images,
    the raw MP4 for video (frames + audio are processed natively in one call, verified
    against a real AI Gateway request)."""
    org_id = ctx.job.org_id
    if creative["format"] == "carousel":
        cards = await ingest_repo.list_assets_by_kind(
            ctx.db, org_id, creative["id"], "original_image"
        )
        images = []
        for card in cards:
            raw = await ctx.blobs.get(card["path"])
            jpeg, _, _ = prepare_image(raw)
            images.append((jpeg, "image/jpeg"))
        return images, None
    data = await ctx.blobs.get(creative["primary_asset_path"])
    if creative["format"] == "video":
        return [], (data, "video/mp4")
    jpeg, _, _ = prepare_image(data)
    return [(jpeg, "image/jpeg")], None


async def _prepare_inputs(ctx: JobContext, creative):
    if creative["format"] == "video":
        # No keyframe extraction or separate transcription any more. Old keyframe/transcript
        # assets from a creative tagged before the switch are cleaned up rather than left as
        # orphaned rows.
        await ingest_repo.delete_assets_by_kind(
            ctx.db, ctx.job.org_id, creative["id"], ("keyframe", "transcript")
        )
    return await load_media(ctx, creative)


async def tag_creative(ctx: JobContext, creative_id: UUID, tagger: Tagger) -> UUID | None:
    org_id = ctx.job.org_id
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    if creative is None or not creative["primary_asset_path"]:
        raise RuntimeError(f"creative {creative_id} has no primary asset yet")
    taxonomy = load_taxonomy()
    tv_id = await repo.ensure_taxonomy_version(ctx.db, taxonomy)
    fmt = creative["format"]
    # Video is tagged by a different model (native video input) than images/carousels --
    # record whichever one actually ran, not always tagging_model.
    model = ctx.settings.video_tagging_model if fmt == "video" else ctx.settings.tagging_model
    run_id = await repo.start_tag_run(ctx.db, org_id, creative_id, tv_id, model)
    if run_id is None:
        return None
    try:
        images, video = await _prepare_inputs(ctx, creative)
        copy = {
            k: creative[k]
            for k in ("headline", "primary_text", "description", "cta_type", "link_url")
        }
        text = user_text(taxonomy, fmt, copy)
        schema = taxonomy.json_schema(fmt)
        result = await tagger.tag(images, text, schema, video=video)
        if result.refused:
            raise RuntimeError("model refused to tag creative")
        tags = taxonomy.validate(result.tags, fmt)
        await repo.finish_tag_run(
            ctx.db,
            org_id,
            run_id,
            tags,
            prompt_sha(SYSTEM, text, schema),
            result.input_tokens,
            result.output_tokens,
        )
    except Exception as exc:
        await repo.fail_tag_run(ctx.db, org_id, run_id, str(exc))
        raise
    return run_id
