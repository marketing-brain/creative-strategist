"""Whether a creative is worth a paid tagging call yet.

Tags exist to be compared. A file nobody watched cannot answer a question: it sits below
every floor the analysis applies (2,000 impressions for a rate, 1,000 plays before Meta's
whole-percent curve rounding swamps it), so tagging it buys a label and no evidence. On org 3
that is real money -- 162 of the 307 untagged video files carry under $50 of spend between
them and only 2 of those have the plays to be measured at all.

The one place the rule lives. The sync obeys it before queueing, the job re-checks it before
paying, and the retag endpoint reports it, so a creative is never tagged in one path that
another path would have refused.

Delivery is pooled over the file's copies, because a tag describes the file: ten copies with
300 impressions each are one file with 3,000, and the account read reads them as one.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import asyncpg

from cs.core import queue
from cs.core import repo as core_repo
from cs.core.config import Settings
from cs.core.db import Database
from cs.jobs.registry import JobContext

log = logging.getLogger("cs.tagging")
TAG_JOB = "tagging.tag_creative"

TAGGABLE_FORMATS = ("image", "video", "carousel")
Blocker = Literal[
    "no_asset", "unresolved", "video_tagging_off", "already_tagged", "below_threshold"
]

# A paid run is one that spent tokens. `runner.tag_creative` copies a finished run's tags
# when the same file arrives with the same copy text, and those cost nothing, so they do not
# use up the repeat budget.
_DELIVERY = """
WITH ids AS (
    SELECT peer.id
    FROM creative c
    JOIN asset a ON a.id = c.primary_asset_id
    JOIN creative peer ON peer.org_id = c.org_id AND peer.ad_account_id = c.ad_account_id
                      AND peer.format = c.format
    JOIN asset pa ON pa.id = peer.primary_asset_id AND pa.sha256 = a.sha256
    WHERE c.org_id = $1 AND c.id = $2 AND c.format = ANY($3::text[])
    UNION
    SELECT $2::uuid
)
SELECT
  (SELECT coalesce(sum(impressions), 0) FROM metric_daily WHERE creative_id IN (SELECT id FROM ids))
    AS impressions,
  (SELECT coalesce(sum(video_plays), 0) FROM metric_daily WHERE creative_id IN (SELECT id FROM ids))
    AS plays,
  (SELECT count(*) FROM tag_run r
     JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
    WHERE r.creative_id IN (SELECT id FROM ids) AND r.status = 'done'
      AND coalesce(r.input_tokens, 0) > 0
      AND split_part(v.version, '.', 1)::int >= $4) AS paid_runs
"""


def taxonomy_major(fmt: str) -> int:
    """The profile a format's tags have to come from: video was rewritten for v2, the image
    and carousel dimensions never changed."""
    return 2 if fmt == "video" else 1


def clears_threshold(settings: Settings, fmt: str, impressions: int, plays: int) -> bool:
    """Enough delivery to answer at least one question about this file: impressions for the
    rate metrics, or plays for the retention curve. Video can qualify either way -- a file
    can be seen often and watched rarely, and that itself is the finding."""
    if impressions >= settings.tagging_min_impressions:
        return True
    return fmt == "video" and plays >= settings.tagging_min_video_plays


async def tag_blocker(
    db: Database,
    settings: Settings,
    org_id: UUID,
    creative: asyncpg.Record,
) -> tuple[Blocker | None, dict]:
    """Why this creative should not be tagged right now, or None with what it delivered.

    The caller passes the creative row it already has (`ingest_repo.get_creative`), so the
    gate costs one query. Cheapest checks first; the delivery query only runs when nothing
    structural blocks it.
    """
    fmt = creative["format"]
    if not creative["primary_asset_path"]:
        return "no_asset", {}
    if fmt not in TAGGABLE_FORMATS:
        # 'unresolved' has no proven format, and taxonomy.for_format returns nothing for it,
        # which structured-output APIs reject outright.
        return "unresolved", {}
    if fmt == "video" and not settings.video_tagging_enabled:
        return "video_tagging_off", {}
    row = await db.fetchrow(
        _DELIVERY, org_id, creative["id"], list(TAGGABLE_FORMATS), taxonomy_major(fmt)
    )
    assert row is not None  # the aggregates always return one row
    facts = {
        "impressions": int(row["impressions"]),
        "plays": int(row["plays"]),
        "paid_runs": int(row["paid_runs"]),
        "min_impressions": settings.tagging_min_impressions,
        "min_plays": settings.tagging_min_video_plays if fmt == "video" else None,
        "repeat_limit": settings.tagging_repeat_per_footage,
    }
    if facts["paid_runs"] >= settings.tagging_repeat_per_footage:
        # Repeats are how tagger agreement is measured (cs.insights.account_read.reliability),
        # so the budget is a setting rather than a flat "once": raise it to have every file
        # tagged more than once on purpose, instead of relying on Meta minting copies.
        return "already_tagged", facts
    if not clears_threshold(settings, fmt, facts["impressions"], facts["plays"]):
        return "below_threshold", facts
    return None, facts


def explain(blocker: Blocker, facts: dict) -> str:
    """One line for a log or an API response."""
    if blocker == "below_threshold":
        floors = f"{facts['min_impressions']:,} impressions"
        if facts.get("min_plays"):
            floors += f" or {facts['min_plays']:,} plays"
        return (
            f"delivered {facts['impressions']:,} impressions and {facts['plays']:,} plays, "
            f"under the {floors} a comparison needs"
        )
    if blocker == "already_tagged":
        n = facts["paid_runs"]
        return (
            "this file has already been tagged"
            if n == 1
            else f"this file has been tagged {n} times, the limit per file"
        )
    return {
        "no_asset": "the file has not been downloaded yet",
        "unresolved": "the creative's format is not proven, so there is no taxonomy to tag with",
        "video_tagging_off": "video tagging is switched off",
    }[blocker]


async def enqueue_tagging(ctx: JobContext, creative_id: UUID, *, force: bool = False) -> bool:
    """Queue a tagging job unless the gate refuses. Returns whether it was queued."""
    creative = await core_repo.get_creative(ctx.db, ctx.job.org_id, creative_id)
    if creative is None:
        return False
    if not force:
        blocker, facts = await tag_blocker(ctx.db, ctx.settings, ctx.job.org_id, creative)
        if blocker is not None:
            log.info(
                "not tagging creative %s: %s",
                creative_id,
                explain(blocker, facts),
                extra={"creative_id": str(creative_id), "blocker": blocker},
            )
            return False
    await queue.enqueue(
        ctx.db,
        ctx.job.org_id,
        TAG_JOB,
        {"creative_id": str(creative_id)},
        dedup_key=f"tag:{creative_id}",
    )
    return True
