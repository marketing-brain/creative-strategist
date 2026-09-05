from __future__ import annotations

from uuid import UUID

import httpx

from cs.ingest import service as ingest_service
from cs.jobs.registry import JobContext, handler
from cs.publish.meta import publish_generated_asset


@handler("publish.meta")
async def publish_meta(ctx: JobContext) -> None:
    org_id = ctx.job.org_id
    pj = await ctx.db.fetchrow(
        "SELECT * FROM publish_job WHERE org_id=$1 AND id=$2", org_id, UUID(ctx.job.payload["publish_job_id"])
    )
    await ctx.db.execute("UPDATE publish_job SET status='running' WHERE id=$1", pj["id"])
    acct = await ctx.db.fetchrow("SELECT * FROM ad_account WHERE id=$1", pj["ad_account_id"])
    try:
        async with httpx.AsyncClient(timeout=300) as http:
            connector = ingest_service.CONNECTOR_FACTORY(ctx.settings, acct, http, ctx.blobs)
            creative_id, ad_id = await publish_generated_asset(ctx, connector, pj)
        await ctx.db.execute(
            "UPDATE publish_job SET status='done', platform_creative_id=$2, platform_ad_id=$3, finished_at=now() WHERE id=$1",
            pj["id"],
            creative_id,
            ad_id,
        )
    except Exception as exc:
        await ctx.db.execute(
            "UPDATE publish_job SET status='failed', error=$2, finished_at=now() WHERE id=$1", pj["id"], str(exc)[:2000]
        )
        raise
