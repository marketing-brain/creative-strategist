from __future__ import annotations

import logging
from uuid import UUID

import httpx

from cs.ingest import service as ingest_service
from cs.jobs.registry import JobContext, handler
from cs.publish.meta import publish_generated_asset

log = logging.getLogger("cs.publish")


@handler("publish.meta")
async def publish_meta(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    pj_id = UUID(ctx.job.payload["publish_job_id"])
    pj = await ctx.db.fetchrow("SELECT * FROM publish_job WHERE org_id=$1 AND id=$2", org_id, pj_id)
    if pj is None:
        raise RuntimeError(f"publish_job not found: {pj_id}")
    if pj["status"] != "pending":
        # This job already ran (successfully or not) -- a retry here after the Meta API calls
        # already succeeded but a later step failed (e.g. the final status='done' UPDATE) would
        # blindly call publish_generated_asset again and create a second, real ad on the
        # customer's Meta account. Enqueue-time max_attempts=1 on publish.meta is the primary
        # guard against the queue auto-retrying; this status check covers the remaining case of
        # someone re-enqueuing the same publish_job_id by hand. Incrementally persisting
        # platform_creative_id as soon as it's known (rather than only at the end) would close
        # the rest of the gap but is out of scope here.
        log.warning(
            "publish.meta skipped: publish_job %s already in status %s", pj_id, pj["status"]
        )
        return
    try:
        await ctx.db.execute("UPDATE publish_job SET status='running' WHERE id=$1", pj["id"])
        acct = await ctx.db.fetchrow(
            "SELECT * FROM ad_account WHERE org_id=$1 AND id=$2", org_id, pj["ad_account_id"]
        )
        if acct is None:
            raise RuntimeError("ad_account not found")
        async with httpx.AsyncClient(timeout=300) as http:
            connector = ingest_service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
            creative_id, ad_id = await publish_generated_asset(ctx, connector, pj)
        await ctx.db.execute(
            "UPDATE publish_job SET status='done', platform_creative_id=$2, platform_ad_id=$3, "
            "finished_at=now() WHERE id=$1",
            pj["id"],
            creative_id,
            ad_id,
        )
    except Exception as exc:
        await ctx.db.execute(
            "UPDATE publish_job SET status='failed', error=$2, finished_at=now() WHERE id=$1",
            pj["id"],
            str(exc)[:2000],
        )
        raise
