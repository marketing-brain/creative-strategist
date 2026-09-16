"""Open-vocabulary tags: slug normalization and the per-org canonical vocabulary."""

from __future__ import annotations

import re
from uuid import UUID

from cs.core.db import Database

MAX_SLUG = 48


def normalize(slug: str) -> str:
    """lowercase snake_case: non-alphanumerics become '_', runs collapse, ends are stripped,
    and the result is cut at 48 characters (then stripped again so it never ends in '_')."""
    s = re.sub(r"[^a-z0-9]+", "_", slug.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_SLUG].rstrip("_")


async def canonicalize(
    db: Database, org_id: UUID, dimension: str, slugs: list[str]
) -> dict[str, str]:
    """Map each normalized slug to the org's canonical slug for it, registering unknown
    slugs as their own canonical. Empty slugs are ignored."""
    out: dict[str, str] = {}
    for slug in dict.fromkeys(s for s in slugs if s):
        canonical = await db.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
        )
        if canonical is None:
            await db.execute(
                "INSERT INTO tag_vocab (org_id, dimension, slug, canonical_slug) "
                "VALUES ($1,$2,$3,$3) ON CONFLICT DO NOTHING",
                org_id,
                dimension,
                slug,
            )
            canonical = slug
        out[slug] = canonical
    return out


async def bump_counts(
    db: Database, org_id: UUID, dimension: str, canonical_slugs: list[str]
) -> None:
    for slug in set(canonical_slugs):
        await db.execute(
            "UPDATE tag_vocab SET count = count + 1 "
            "WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND slug = canonical_slug",
            org_id,
            dimension,
            slug,
        )


async def hints(db: Database, org_id: UUID, dimension: str, n: int) -> list[tuple[str, int]]:
    rows = await db.fetch(
        "SELECT slug, count FROM tag_vocab "
        "WHERE org_id=$1 AND dimension=$2 AND slug = canonical_slug AND count > 0 "
        "ORDER BY count DESC, slug LIMIT $3",
        org_id,
        dimension,
        n,
    )
    return [(r["slug"], r["count"]) for r in rows]
