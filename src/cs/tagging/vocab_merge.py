"""Merge near-synonym open-vocabulary slugs (spec 2026-09-16-vocab-merge-design.md).

Embeddings find candidate pairs; a text-only judge decides; a union-find plan applies
or routes to review; every decision is logged in tag_vocab_merge and reversible."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from uuid import UUID

from cs.core.db import Database
from cs.core.embeddings import Embedder, cosine


def embed_text(dimension: str, slug: str) -> str:
    return f"{dimension.replace('_', ' ')}: {slug.replace('_', ' ')}"


def _literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in vector) + "]"


async def embed_missing(
    db: Database, embedder: Embedder, org_id: UUID, dimension: str, model: str
) -> int:
    """Embed every canonical slug of the dimension that has no stored vector for `model`."""
    rows = await db.fetch(
        """
        SELECT v.slug FROM tag_vocab v
        LEFT JOIN tag_vocab_embedding e
          ON e.org_id = v.org_id AND e.dimension = v.dimension AND e.slug = v.slug AND e.model = $3
        WHERE v.org_id = $1 AND v.dimension = $2 AND v.slug = v.canonical_slug AND e.slug IS NULL
        ORDER BY v.slug
        """,
        org_id,
        dimension,
        model,
    )
    slugs = [r["slug"] for r in rows]
    if not slugs:
        return 0
    vectors = await embedder.embed([embed_text(dimension, s) for s in slugs])
    await db.executemany(
        "INSERT INTO tag_vocab_embedding (org_id, dimension, slug, model, embedding) "
        "VALUES ($1, $2, $3, $4, $5::vector) ON CONFLICT DO NOTHING",
        [(org_id, dimension, s, model, _literal(v)) for s, v in zip(slugs, vectors, strict=True)],
    )
    return len(slugs)


async def load_vectors(
    db: Database, org_id: UUID, dimension: str, model: str
) -> dict[str, list[float]]:
    rows = await db.fetch(
        """
        SELECT e.slug, e.embedding::text AS embedding
        FROM tag_vocab_embedding e
        JOIN tag_vocab v ON v.org_id = e.org_id AND v.dimension = e.dimension AND v.slug = e.slug
        WHERE e.org_id = $1 AND e.dimension = $2 AND e.model = $3 AND v.slug = v.canonical_slug
        """,
        org_id,
        dimension,
        model,
    )
    return {r["slug"]: json.loads(r["embedding"]) for r in rows}


@dataclass(frozen=True)
class Pair:
    a: str
    b: str
    cosine: float


async def rejected_pairs(db: Database, org_id: UUID, dimension: str) -> set[tuple[str, str]]:
    rows = await db.fetch(
        "SELECT slug, into_slug FROM tag_vocab_merge "
        "WHERE org_id = $1 AND dimension = $2 AND status = 'rejected'",
        org_id,
        dimension,
    )
    return {tuple(sorted((r["slug"], r["into_slug"]))) for r in rows}


async def candidate_pairs(
    db: Database, org_id: UUID, dimension: str, model: str, floor: float = 0.70
) -> list[Pair]:
    vectors = await load_vectors(db, org_id, dimension, model)
    rejected = await rejected_pairs(db, org_id, dimension)
    out: list[Pair] = []
    for a, b in itertools.combinations(sorted(vectors), 2):
        if (a, b) in rejected:
            continue
        c = cosine(vectors[a], vectors[b])
        if c >= floor:
            out.append(Pair(a, b, c))
    out.sort(key=lambda p: (-p.cosine, p.a, p.b))
    return out
