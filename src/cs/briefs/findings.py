"""What works findings as inputs to making: which cluster a video is in, the findings of that
cluster, and a stable id for each finding so a page can hand one to a brief.

Findings are the reviewed profile reads of the tag-clusters experiment: each compares videos of
the same kind, which is the only comparison the product shows (account-wide reads confused
format with content)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from uuid import UUID

from cs.core.db import Database
from cs.experiments import tag_clusters as tc

SLUG = "tag-clusters"


def finding_id(cluster: str, title: str) -> str:
    return hashlib.sha1(f"{cluster}\n{title}".encode()).hexdigest()[:12]


async def _experiment(db: Database, org_id: UUID) -> UUID | None:
    return await db.fetchval("SELECT id FROM experiment WHERE org_id=$1 AND slug=$2", org_id, SLUG)


async def all_findings(db: Database, org_id: UUID) -> dict[str, dict]:
    """id -> the finding with its cluster."""
    eid = await _experiment(db, org_id)
    if eid is None:
        return {}
    out: dict[str, dict] = {}
    for r in await db.fetch(
        "SELECT key, payload FROM experiment_result WHERE experiment_id=$1 AND key LIKE 'profile_read:%'",
        eid,
    ):
        cluster = r["key"].split(":", 1)[1]
        for f in r["payload"].get("reviewed") or []:
            fid = finding_id(cluster, f["title"])
            out[fid] = {
                "id": fid,
                "cluster": cluster,
                "title": f["title"],
                "body": f["body"],
                "strength": f.get("strength"),
                "basis": f.get("basis"),
                "numbers": f.get("numbers") or [],
            }
    return out


async def cluster_members(db: Database, org_id: UUID) -> dict[UUID, str]:
    """footage id -> cluster, computed the way the clusters view does (small clusters fold into Other)."""
    eid = await _experiment(db, org_id)
    if eid is None:
        return {}
    labels: dict[UUID, dict[str, str]] = defaultdict(dict)
    for r in await db.fetch(
        "SELECT footage_id, axis, value FROM experiment_label WHERE experiment_id=$1 AND axis IN ('who','animated','reveal')",
        eid,
    ):
        labels[r["footage_id"]][r["axis"]] = r["value"]
    return tc.cluster_map(dict(labels))


async def cluster_of(db: Database, org_id: UUID, footage_id: UUID) -> str | None:
    """The video's cluster."""
    return (await cluster_members(db, org_id)).get(footage_id)


async def guardrails(db: Database, org_id: UUID, footage_id: UUID) -> tuple[str | None, list[dict]]:
    """The findings a new brief for this video should respect: what holds up in its cluster and
    what every video there has in common."""
    cluster = await cluster_of(db, org_id, footage_id)
    if not cluster or cluster == tc.OTHER:
        return cluster, []
    return cluster, [
        f
        for f in (await all_findings(db, org_id)).values()
        if f["cluster"] == cluster and f["strength"] in ("holds", "context")
    ]
