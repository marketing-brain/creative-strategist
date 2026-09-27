"""briefs.generate: measure the parent, make the one paid call, check it, store the brief."""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

import openai

from cs.briefs import facts as facts_mod
from cs.briefs import findings as findings_mod
from cs.briefs import generate as gen
from cs.briefs import render as render_mod
from cs.jobs.registry import JobContext, handler

JOB = "briefs.generate"
RENDER_JOB = "briefs.render"
log = logging.getLogger(__name__)


@handler(JOB)
async def generate_brief(ctx: JobContext) -> None:
    brief_id = UUID(ctx.job.payload["brief_id"])
    row = await ctx.db.fetchrow(
        "SELECT org_id, ad_account_id, footage_id, status, focus FROM creative_brief WHERE id=$1",
        brief_id,
    )
    if row is None or row["status"] not in ("queued", "running"):
        return
    await ctx.db.execute(
        "UPDATE creative_brief SET status='running', updated_at=now() WHERE id=$1", brief_id
    )
    try:
        f = await facts_mod.parent_facts(
            ctx.db, ctx.blobs, row["org_id"], row["ad_account_id"], row["footage_id"], ctx.settings
        )
        _, rails = await findings_mod.guardrails(ctx.db, row["org_id"], row["footage_id"])
        client = openai.AsyncOpenAI(
            api_key=ctx.settings.ai_gateway_api_key,
            base_url=f"{ctx.settings.ai_gateway_base_url}/v1",
            max_retries=0,
            timeout=900,
        )
        out, usage = await gen.call(
            ctx.db,
            client,
            ctx.settings.brief_model,
            row["org_id"],
            brief_id,
            gen.prompt(f, row["focus"], rails),
        )
        checked, dropped = gen.check(out, f)
        content = gen.compose(checked, f, dropped)
    except Exception as exc:  # noqa: BLE001 - the page shows the failure; nothing retries a paid call
        log.exception("brief failed", extra={"brief_id": str(brief_id)})
        await ctx.db.execute(
            "UPDATE creative_brief SET status='failed', error=$2, updated_at=now() WHERE id=$1",
            brief_id,
            str(exc)[:1000],
        )
        return
    await ctx.db.execute(
        """
        UPDATE creative_brief SET status='done', error=NULL, title=$2, facts=$3::jsonb, content=$4::jsonb,
               generated=$5::jsonb, updated_at=now()
        WHERE id=$1
        """,
        brief_id,
        content["title"],
        f,
        content,
        {"raw": out, "usage": usage},
    )


@handler(RENDER_JOB)
async def render_variant(ctx: JobContext) -> None:
    """Cut one variant from the parent. Free, so a failure can simply be rendered again."""
    rid = UUID(ctx.job.payload["render_id"])
    r = await ctx.db.fetchrow(
        "SELECT r.*, b.content->>'offer' AS offer FROM brief_render r JOIN creative_brief b ON b.id = r.brief_id"
        " WHERE r.id=$1",
        rid,
    )
    if r is None or r["status"] not in ("queued", "running"):
        return
    await ctx.db.execute("UPDATE brief_render SET status='running' WHERE id=$1", rid)
    try:
        data, meta = await asyncio.to_thread(
            render_mod.render, await ctx.blobs.get(r["source"]), r["edit"], r["offer"] or ""
        )
        path = f"{r['org_id']}/briefs/{r['brief_id']}/{r['code']}-{rid}.mp4"
        await ctx.blobs.put(path, data, "video/mp4")
    except Exception as exc:  # noqa: BLE001 - shown on the page; rendering again is free
        log.exception("render failed", extra={"render_id": str(rid)})
        await ctx.db.execute(
            "UPDATE brief_render SET status='failed', error=$2, finished_at=now() WHERE id=$1",
            rid,
            str(exc)[:1000],
        )
        return
    await ctx.db.execute(
        "UPDATE brief_render SET status='done', path=$2, width=$3, height=$4, duration_s=$5, error=NULL,"
        " finished_at=now() WHERE id=$1",
        rid,
        path,
        meta["width"],
        meta["height"],
        meta["duration_s"],
    )
