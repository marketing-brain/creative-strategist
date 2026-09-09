from __future__ import annotations

import logging
from uuid import UUID

import httpx

from cs.core.config import Settings
from cs.jobs.registry import JobContext, handler
from cs.tagging import runner
from cs.tagging.tagger import GeminiGatewayTagger, Tagger
from cs.tagging.video import GatewayTranscriber, Transcriber

log = logging.getLogger("cs.tagging")


def default_tagger_factory(settings: Settings) -> Tagger:
    return GeminiGatewayTagger(settings)


def default_transcriber_factory(settings: Settings, http: httpx.AsyncClient) -> Transcriber:
    return GatewayTranscriber(settings, http)


TAGGER_FACTORY = default_tagger_factory
TRANSCRIBER_FACTORY = default_transcriber_factory


@handler("tagging.tag_creative")
async def tag_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    async with httpx.AsyncClient(timeout=300) as http:
        await runner.tag_creative(
            ctx, creative_id, TAGGER_FACTORY(ctx.settings), TRANSCRIBER_FACTORY(ctx.settings, http)
        )


from cs.ingest import repo as ingest_repo  # noqa: E402
from cs.tagging import repo as tag_repo  # noqa: E402
from cs.tagging.embed import Embedder, GatewayEmbedder, tag_text  # noqa: E402


def default_embedder_factory(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    return GatewayEmbedder(settings, http)


EMBEDDER_FACTORY = default_embedder_factory


@handler("tagging.embed_creative")
async def embed_creative(ctx: JobContext) -> None:
    creative_id = UUID(ctx.job.payload["creative_id"])
    org_id = ctx.job.org_id
    run_id = await tag_repo.latest_run_id(ctx.db, org_id, creative_id)
    if run_id is None:
        raise RuntimeError("no completed tag run to embed")
    creative = await ingest_repo.get_creative(ctx.db, org_id, creative_id)
    tags = await tag_repo.latest_tags(ctx.db, org_id, creative_id)
    text = tag_text(
        {
            "headline": creative["headline"],
            "primary_text": creative["primary_text"],
        },
        tags,
    )
    async with httpx.AsyncClient() as http:
        [vec] = await EMBEDDER_FACTORY(ctx.settings, http).embed([text])
    await tag_repo.upsert_embedding(ctx.db, org_id, creative_id, run_id, vec)


from datetime import UTC, datetime, timedelta  # noqa: E402

from cs.core import queue  # noqa: E402
from cs.core.db import Database  # noqa: E402
from cs.core.ids import new_id  # noqa: E402
from cs.tagging.batch import BatchTagger, GeminiGatewayBatchTagger  # noqa: E402
from cs.tagging.images import prepare_image  # noqa: E402
from cs.tagging.prompts import SYSTEM, prompt_sha, user_text  # noqa: E402
from cs.tagging.taxonomy import load_taxonomy  # noqa: E402


def default_batch_factory(settings: Settings, db: Database, org_id: UUID) -> BatchTagger:
    return GeminiGatewayBatchTagger(settings, db, org_id)


BATCH_FACTORY = default_batch_factory


@handler("tagging.batch_submit")
async def batch_submit(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    taxonomy = load_taxonomy()
    tv = await tag_repo.ensure_taxonomy_version(ctx.db, taxonomy)
    model = ctx.settings.tagging_model
    ids = await tag_repo.untagged_creative_ids(
        ctx.db, org_id, tv, model, int(ctx.job.payload.get("limit", 500))
    )
    if not ids:
        return
    requests, items, started_run_ids = [], [], []
    for cid in ids:
        run_id = None
        try:
            creative = await ingest_repo.get_creative(ctx.db, org_id, cid)
            run_id = await tag_repo.start_tag_run(ctx.db, org_id, cid, tv, model)
            if run_id is None:
                continue
            started_run_ids.append(run_id)
            fmt = creative["format"]
            jpeg, _, _ = prepare_image(await ctx.blobs.get(creative["primary_asset_path"]))
            copy = {
                k: creative[k]
                for k in ("headline", "primary_text", "description", "cta_type", "link_url")
            }
            text = user_text(taxonomy, fmt, copy, None, None)
            schema = taxonomy.json_schema(fmt)
            requests.append(
                {
                    "custom_id": str(run_id),
                    "images": [(jpeg, "image/jpeg")],
                    "user_text": text,
                    "schema": schema,
                }
            )
            items.append((run_id, prompt_sha(SYSTEM, text, schema)))
        except Exception as exc:
            # A single corrupt/unreadable creative must not abort the whole
            # batch (Global Constraint: per-creative failures are isolated).
            # Only fail the run that was actually started for this creative,
            # then move on to the next one.
            log.warning("batch_submit: creative %s failed to prepare: %s", cid, exc)
            if run_id is not None:
                await tag_repo.fail_tag_run(ctx.db, org_id, run_id, str(exc))
            continue
    if not requests:
        return
    try:
        provider_id = await BATCH_FACTORY(ctx.settings, ctx.db, org_id).submit(requests)
    except Exception as exc:
        # A submit()-level failure legitimately means the whole batch failed:
        # every run started above must not be left stuck, so fail them all.
        for run_id in started_run_ids:
            await tag_repo.fail_tag_run(ctx.db, org_id, run_id, str(exc))
        raise
    batch_id = new_id()
    await ctx.db.execute(
        "INSERT INTO tag_batch (id, org_id, provider_batch_id, taxonomy_version_id, model, "
        "status, request_count) VALUES ($1,$2,$3,$4,$5,'submitted',$6)",
        batch_id,
        org_id,
        provider_id,
        tv,
        model,
        len(requests),
    )
    for run_id, sha in items:
        await ctx.db.execute(
            "INSERT INTO tag_batch_item (tag_batch_id, custom_id, tag_run_id) VALUES ($1,$2,$3)",
            batch_id,
            str(run_id),
            run_id,
        )
        await ctx.db.execute("UPDATE tag_run SET prompt_sha256=$2 WHERE id=$1", run_id, sha)
    await queue.enqueue(
        ctx.db,
        org_id,
        "tagging.batch_poll",
        {"tag_batch_id": str(batch_id)},
        dedup_key=f"poll:{batch_id}",
        run_after=datetime.now(UTC) + timedelta(seconds=60),
    )


@handler("tagging.batch_poll")
async def batch_poll(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    batch_id = UUID(ctx.job.payload["tag_batch_id"])
    batch = await ctx.db.fetchrow(
        "SELECT * FROM tag_batch WHERE id=$1 AND org_id=$2", batch_id, org_id
    )
    if batch["status"] == "ended":
        return  # already finished by an earlier poll; nothing left to do
    ended, results = await BATCH_FACTORY(ctx.settings, ctx.db, org_id).poll(
        batch["provider_batch_id"]
    )
    if not ended:
        # No dedup_key here: the job currently executing this handler is still
        # 'running' under dedup_key f"poll:{batch_id}" (set by batch_submit, or
        # by a prior iteration of this same reschedule), so re-using that key
        # would collide with our own row and silently drop the reschedule.
        await queue.enqueue(
            ctx.db,
            org_id,
            "tagging.batch_poll",
            {"tag_batch_id": str(batch_id)},
            run_after=datetime.now(UTC) + timedelta(seconds=120),
        )
        return
    claimed = await ctx.db.fetchrow(
        "UPDATE tag_batch SET status='ended', ended_at=now() WHERE id=$1 AND status<>'ended' "
        "RETURNING id",
        batch_id,
    )
    if claimed is None:
        return  # another worker already finished this batch
    taxonomy = load_taxonomy()
    for res in results:
        run_id = UUID(res["custom_id"])
        if res["ok"]:
            row = await ctx.db.fetchrow(
                "SELECT tr.prompt_sha256 AS prompt_sha256, c.format AS format "
                "FROM tag_run tr JOIN creative c ON c.id = tr.creative_id WHERE tr.id=$1",
                run_id,
            )
            sha = row["prompt_sha256"]
            await tag_repo.finish_tag_run(
                ctx.db,
                org_id,
                run_id,
                taxonomy.validate(res["tags"], row["format"]),
                sha or "",
                res["input_tokens"],
                res["output_tokens"],
            )
        else:
            await tag_repo.fail_tag_run(ctx.db, org_id, run_id, res["error"] or "batch item failed")
