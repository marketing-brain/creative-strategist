from __future__ import annotations

import logging

from cs.connectors.base import AdPlatformConnector
from cs.generation import repo as generation_repo
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.publish")


def build_creative_spec(
    asset: dict, form: dict, image_hash: str | None, video_id: str | None
) -> dict:
    headline = form.get("headline") or asset["headline"]
    text = form.get("primary_text") or asset["primary_text"]
    cta = {
        "type": form.get("cta_type") or asset["cta_type"] or "LEARN_MORE",
        "value": {"link": form["link_url"]},
    }
    if asset["kind"] == "video":
        story = {
            "video_data": {
                "video_id": video_id,
                "title": headline,
                "message": text,
                "call_to_action": cta,
                "image_hash": image_hash,
            }
            if image_hash
            else {
                "video_id": video_id,
                "title": headline,
                "message": text,
                "call_to_action": cta,
            }
        }
    else:
        story = {
            "link_data": {
                "image_hash": image_hash,
                "link": form["link_url"],
                "name": headline,
                "message": text,
                "call_to_action": cta,
            }
        }
    return {"object_story_spec": {"page_id": form["page_id"], **story}}


async def publish_generated_asset(
    ctx: JobContext, connector: AdPlatformConnector, pj
) -> tuple[str, str]:
    org_id = ctx.job.org_id
    asset = await ctx.db.fetchrow(
        "SELECT * FROM generated_asset WHERE org_id=$1 AND id=$2", org_id, pj["generated_asset_id"]
    )
    if asset["review_state"] != "approved":
        raise RuntimeError("asset is not approved")
    acct = await ctx.db.fetchrow(
        "SELECT * FROM ad_account WHERE org_id=$1 AND id=$2", org_id, pj["ad_account_id"]
    )
    data = await ctx.blobs.get(asset["path"])
    account_id = acct["platform_account_id"]
    image_hash = video_id = None
    if asset["kind"] == "video":
        video_id = await connector.upload_video(account_id, data, f"{asset['id']}.mp4")
        # Meta's object_story_spec.video_data requires a thumbnail image_hash or the create-ad
        # call is rejected. The generation pipeline writes a 9:16 still alongside each video at
        # the same generation_id/variant (it's the seed frame the video was generated from) --
        # reuse it as the thumbnail rather than leaving image_hash unset.
        still = await generation_repo.get_generated_asset_by_variant(
            ctx.db, org_id, asset["generation_id"], "image", "9:16", asset["variant"]
        )
        if still:
            still_data = await ctx.blobs.get(still["path"])
            image_hash = await connector.upload_image(account_id, still_data, f"{still['id']}.jpg")
        else:
            log.warning(
                "no still image found to use as video thumbnail for generated_asset %s "
                "(generation %s, variant %s) -- Meta may reject this create-ad call",
                asset["id"],
                asset["generation_id"],
                asset["variant"],
            )
    else:
        image_hash = await connector.upload_image(account_id, data, f"{asset['id']}.jpg")
    spec = build_creative_spec(dict(asset), pj["form"], image_hash, video_id)
    name = f"CS {asset['headline'][:40]} {str(asset['id'])[-6:]}"
    return await connector.create_ad(
        account_id, pj["adset_id"], name, spec, pj["ad_status_requested"]
    )
