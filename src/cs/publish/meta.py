from __future__ import annotations

from cs.connectors.base import AdPlatformConnector
from cs.jobs.registry import JobContext


def build_creative_spec(asset: dict, form: dict, image_hash: str | None, video_id: str | None) -> dict:
    headline = form.get("headline") or asset["headline"]
    text = form.get("primary_text") or asset["primary_text"]
    cta = {"type": form.get("cta_type") or asset["cta_type"] or "LEARN_MORE", "value": {"link": form["link_url"]}}
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


async def publish_generated_asset(ctx: JobContext, connector: AdPlatformConnector, pj) -> tuple[str, str]:
    org_id = ctx.job.org_id
    asset = await ctx.db.fetchrow(
        "SELECT * FROM generated_asset WHERE org_id=$1 AND id=$2", org_id, pj["generated_asset_id"]
    )
    if asset["review_state"] != "approved":
        raise RuntimeError("asset is not approved")
    acct = await ctx.db.fetchrow("SELECT * FROM ad_account WHERE org_id=$1 AND id=$2", org_id, pj["ad_account_id"])
    data = await ctx.blobs.get(asset["path"])
    account_id = acct["platform_account_id"]
    image_hash = video_id = None
    if asset["kind"] == "video":
        video_id = await connector.upload_video(account_id, data, f"{asset['id']}.mp4")
    else:
        image_hash = await connector.upload_image(account_id, data, f"{asset['id']}.jpg")
    spec = build_creative_spec(dict(asset), pj["form"], image_hash, video_id)
    name = f"CS {asset['headline'][:40]} {str(asset['id'])[-6:]}"
    return await connector.create_ad(account_id, pj["adset_id"], name, spec, pj["ad_status_requested"])
