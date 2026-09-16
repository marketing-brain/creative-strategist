from __future__ import annotations

from uuid import UUID

from cs.core import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.tagging import repo, vocab
from cs.tagging.images import prepare_image
from cs.tagging.prompts import SYSTEM, SYSTEM_V2, prompt_sha, user_text, user_text_nested
from cs.tagging.tagger import Tagger, TagResult
from cs.tagging.taxonomy import Taxonomy, computed_rows, load_taxonomy, reconcile_captions
from cs.tagging.video import aspect_label, cuts_per_10s, probe_video


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


COPY_FIELDS = ("headline", "primary_text", "description", "cta_type", "link_url")


async def profile_video(
    ctx: JobContext,
    creative,
    taxonomy: Taxonomy,
    tagger: Tagger,
    hints: dict[str, list[tuple[str, int]]],
) -> tuple[list[dict], TagResult, str, dict]:
    """One model call for a video's v2 profile plus the computed layer. Writes nothing:
    open rows come back normalized but not canonicalized, so the consistency script can
    call this twice and compare. Raises on refusal or a rule break."""
    data = await ctx.blobs.get(creative["primary_asset_path"])
    stats = probe_video(data)
    copy = {k: creative[k] for k in COPY_FIELDS}
    text = user_text_nested(taxonomy, "video", copy, hints, duration_s=stats.duration_s)
    schema = taxonomy.json_schema("video")
    result = await tagger.tag([], text, schema, video=(data, "video/mp4"), system=SYSTEM_V2)
    if result.refused:
        raise RuntimeError("model refused to tag creative")
    rows = reconcile_captions(taxonomy.validate(result.payload, "video"))
    late = [r for r in rows if r["t_start_s"] is not None and r["t_start_s"] > stats.duration_s + 1]
    if late:
        raise ValueError(
            f"{late[0]['dimension']}: t_start_s {late[0]['t_start_s']} is beyond the video length "
            f"{stats.duration_s:.1f}s"
        )
    rows += computed_rows(
        taxonomy,
        "video",
        {
            "duration_s": round(stats.duration_s, 1),
            "cuts_per_10s": cuts_per_10s(stats),
            "aspect_ratio": aspect_label(stats.width, stats.height),
        },
    )
    return rows, result, text, schema


async def _canonicalize_open_rows(
    ctx: JobContext, taxonomy: Taxonomy, rows: list[dict]
) -> list[dict]:
    """Map open slugs to the org's canonical slugs and drop duplicates that collapse."""
    org_id = ctx.job.org_id
    out: list[dict] = []
    for d in taxonomy.open_dimensions("video"):
        mine = [r for r in rows if r["dimension"] == d.name]
        mapping = await vocab.canonicalize(ctx.db, org_id, d.name, [r["value"] for r in mine])
        seen: set[str] = set()
        for r in mine:
            canonical = mapping[r["value"]]
            if canonical in seen:
                continue
            seen.add(canonical)
            out.append({**r, "value": canonical})
    return [r for r in rows if r["kind"] != "open"] + out


async def _tag_video(
    ctx: JobContext, creative, taxonomy: Taxonomy, tagger: Tagger, run_id: UUID
) -> None:
    org_id = ctx.job.org_id
    await ingest_repo.delete_assets_by_kind(
        ctx.db, org_id, creative["id"], ("keyframe", "transcript")
    )
    hints = {
        d.name: await vocab.hints(ctx.db, org_id, d.name, d.vocab_hint)
        for d in taxonomy.open_dimensions("video")
        if d.vocab_hint
    }
    rows, result, text, schema = await profile_video(ctx, creative, taxonomy, tagger, hints)
    rows = await _canonicalize_open_rows(ctx, taxonomy, rows)
    await repo.finish_tag_run(
        ctx.db,
        org_id,
        run_id,
        rows,
        prompt_sha(SYSTEM_V2, text, schema),
        result.input_tokens,
        result.output_tokens,
    )
    for d in taxonomy.open_dimensions("video"):
        await vocab.bump_counts(
            ctx.db, org_id, d.name, [r["value"] for r in rows if r["dimension"] == d.name]
        )


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
        if taxonomy.is_nested(fmt):
            await _tag_video(ctx, creative, taxonomy, tagger, run_id)
            return run_id
        images, video = await _prepare_inputs(ctx, creative)
        copy = {k: creative[k] for k in COPY_FIELDS}
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
