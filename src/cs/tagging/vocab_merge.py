"""Merge near-synonym open-vocabulary slugs (spec 2026-09-16-vocab-merge-design.md).

Embeddings find candidate pairs; a text-only judge decides; a union-find plan applies
or routes to review; every decision is logged in tag_vocab_merge and reversible."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import openai

from cs.core.config import Settings
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


@dataclass(frozen=True)
class SlugContext:
    count: int
    evidence: list[str]


async def slug_context(
    db: Database, org_id: UUID, dimension: str, slugs: list[str]
) -> dict[str, SlugContext]:
    counts = {
        r["slug"]: r["count"]
        for r in await db.fetch(
            "SELECT slug, count FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug = ANY($3)",
            org_id,
            dimension,
            slugs,
        )
    }
    rows = await db.fetch(
        """
        SELECT value, evidence FROM (
          SELECT value, evidence, row_number() OVER (PARTITION BY value ORDER BY id) AS rn
          FROM tag WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value = ANY($3)
            AND evidence <> ''
        ) t WHERE rn <= 3
        """,
        org_id,
        dimension,
        slugs,
    )
    evidence: dict[str, list[str]] = {}
    for r in rows:
        evidence.setdefault(r["value"], []).append(r["evidence"])
    return {s: SlugContext(counts.get(s, 0), evidence.get(s, [])) for s in slugs}


@dataclass(frozen=True)
class Verdict:
    pair: Pair
    same_concept: bool
    reason: str


class Judge(Protocol):
    async def judge(
        self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
    ) -> list[Verdict]: ...


JUDGE_SYSTEM = (
    "You decide whether two short tags from an ad-creative taxonomy name the same concept "
    "for the purpose of grouping ads. Same concept means a strategist would treat the two "
    "as interchangeable labels, not merely related. An ingredient and a product containing "
    "it are different. A brand and a category are different. A claim and its negation are "
    "different. A specific value and its general category are different. Answer every pair."
)


def judge_user_text(
    dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
) -> str:
    lines = [f"Dimension: {dimension}", description, "", "Tags (count): evidence from the ads"]
    for slug in sorted({s for p in pairs for s in (p.a, p.b)}):
        c = context.get(slug, SlugContext(0, []))
        ev = " | ".join(c.evidence) if c.evidence else "(no evidence stored)"
        lines.append(f"- {slug} (count {c.count}): {ev}")
    lines += ["", "Candidate pairs:"]
    for p in pairs:
        lines.append(f"{p.a} | {p.b} | cosine {p.cosine:.2f}")
    lines += ["", "Return JSON matching the schema exactly, one entry per candidate pair."]
    return "\n".join(lines)


def judge_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "pairs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "b": {"type": "string"},
                        "same_concept": {"type": "boolean"},
                        "reason": {"type": "string"},
                    },
                    "required": ["a", "b", "same_concept", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["pairs"],
        "additionalProperties": False,
    }


class GatewayJudge:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.tagging_model  # text-only, the cheap tier

    async def judge(
        self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
    ) -> list[Verdict]:
        if not pairs:
            return []
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": judge_user_text(dimension, description, pairs, context),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "verdicts", "schema": judge_schema(), "strict": True},
            },
        )
        text = resp.choices[0].message.content or "{}"
        answered = {}
        for item in json.loads(text).get("pairs", []):
            key = tuple(sorted((str(item.get("a", "")), str(item.get("b", "")))))
            answered[key] = (bool(item.get("same_concept")), str(item.get("reason", "")))
        out: list[Verdict] = []
        for p in pairs:
            same, reason = answered.get((p.a, p.b), (False, "no verdict returned"))
            out.append(Verdict(p, same, reason))
        return out


class FakeJudge:
    def __init__(self, same: set[tuple[str, str]]):
        self.same = {tuple(sorted(s)) for s in same}

    async def judge(self, dimension, description, pairs, context) -> list[Verdict]:
        return [Verdict(p, (p.a, p.b) in self.same, "fake") for p in pairs]
