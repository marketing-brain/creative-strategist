from __future__ import annotations

from uuid import UUID

from cs.core import queue
from cs.core.blobs import sha256_hex
from cs.ingest import repo as ingest_repo
from cs.jobs.registry import JobContext
from cs.tagging import repo
from cs.tagging.images import prepare_image
from cs.tagging.prompts import SYSTEM, prompt_sha, user_text
from cs.tagging.tagger import Tagger
from cs.tagging.taxonomy import load_taxonomy
from cs.tagging.video import Transcriber, extract_audio, extract_keyframes, probe_duration_ms


async def _prepare_inputs(ctx: JobContext, creative, transcriber: Transcriber):
    org_id = ctx.job.org_id
    data = await ctx.blobs.get(creative["primary_asset_path"])
    if creative["format"] == "video":
        frames = extract_keyframes(data)
        images = [(f.jpeg, "image/jpeg") for f in frames]
        stamps = [f.timestamp for f in frames]
        for i, f in enumerate(frames):
            key = f"{org_id}/creatives/{creative['id']}/keyframes/{i:02d}.jpg"
            await ctx.blobs.put(key, f.jpeg, "image/jpeg")
            await ingest_repo.insert_asset(
                ctx.db,
                org_id,
                creative["id"],
                "keyframe",
                key,
                sha256_hex(f.jpeg),
                "image/jpeg",
                f.width,
                f.height,
                int(f.timestamp * 1000),
                seq=i,
            )
        audio = extract_audio(data)
        transcript = await transcriber.transcribe(audio, "audio/mpeg") if audio else ""
        tkey = f"{org_id}/creatives/{creative['id']}/transcript.txt"
        await ctx.blobs.put(tkey, transcript.encode(), "text/plain")
        await ingest_repo.insert_asset(
            ctx.db,
            org_id,
            creative["id"],
            "transcript",
            tkey,
            sha256_hex(transcript.encode()),
            "text/plain",
            duration_ms=probe_duration_ms(data),
        )
        return images, transcript, stamps
    jpeg, _, _ = prepare_image(data)
    return [(jpeg, "image/jpeg")], None, None


async def tag_creative(
    ctx: JobContext, creative_id: UUID, tagger: Tagger, transcriber: Transcriber
) -> UUID | None:
    org_id = ctx.job.org_id
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    if creative is None or not creative["primary_asset_path"]:
        raise RuntimeError(f"creative {creative_id} has no primary asset yet")
    taxonomy = load_taxonomy()
    tv_id = await repo.ensure_taxonomy_version(ctx.db, taxonomy)
    model = ctx.settings.claude_model_tagging
    run_id = await repo.start_tag_run(ctx.db, org_id, creative_id, tv_id, model)
    if run_id is None:
        return None
    try:
        images, transcript, stamps = await _prepare_inputs(ctx, creative, transcriber)
        fmt = creative["format"]
        copy = {
            k: creative[k]
            for k in ("headline", "primary_text", "description", "cta_type", "link_url")
        }
        text = user_text(taxonomy, fmt, copy, transcript, stamps)
        schema = taxonomy.json_schema(fmt)
        result = await tagger.tag(images, text, schema)
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
    await queue.enqueue(
        ctx.db,
        org_id,
        "tagging.embed_creative",
        {"creative_id": str(creative_id)},
        dedup_key=f"embed:{creative_id}",
    )
    return run_id
