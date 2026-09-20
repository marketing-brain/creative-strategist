from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

import asyncpg

from cs.core.config import Settings
from cs.core.db import Database
from cs.ingest.durations import unprobed_video_creatives
from cs.insights import repo
from cs.insights.creative_insight import is_readable
from cs.insights.diagnose_creative import DiagnosedCreative, diagnose_creative

FORMATS = ("image", "video", "carousel")
NIGHTLY_SCHEDULE = "insights.nightly_creative_insights"

Blocker = Literal["no_asset", "unresolved", "format_limit", "lengths_pending", "unreadable"]


async def insight_blocker(
    db: Database,
    settings: Settings,
    org_id: UUID,
    creative: asyncpg.Record,
    until: date,
    member_ids: list[UUID] | None = None,
) -> tuple[Blocker | None, DiagnosedCreative | None]:
    """Why this creative cannot get an insight right now, or None with its diagnosis when it
    can. The one place the gates live: the job obeys it and the API reports it, so the empty
    state on the creative page always names the reason the job would give. Cheapest gates
    first; the diagnosis only runs once nothing else blocks."""
    if not creative["primary_asset_path"]:
        return "no_asset", None
    fmt = creative["format"]
    if fmt not in FORMATS:
        return "unresolved", None
    cap = settings.creative_insight_limit_per_format
    if await repo.count_creative_insights(db, org_id, fmt) >= cap:
        return "format_limit", None
    if fmt == "video" and await unprobed_video_creatives(
        db, 1, creative["ad_account_id"], exclude_creative_id=creative["id"]
    ):
        # Retention compares only videos of similar length. Writing this once while peers'
        # lengths are still being backfilled would leave retention unreadable for good;
        # the nightly sweep tries again once they are probed.
        return "lengths_pending", None
    diagnosed = await diagnose_creative(db, org_id, creative, until, member_ids=member_ids)
    if not is_readable(diagnosed):
        return "unreadable", None
    return None, diagnosed
