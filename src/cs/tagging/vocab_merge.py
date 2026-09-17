"""Merge near-synonym open-vocabulary slugs (spec 2026-09-16-vocab-merge-design.md).

Embeddings find candidate pairs; a text-only judge decides; a union-find plan applies
or routes to review; every decision is logged in tag_vocab_merge and reversible."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import asyncpg
import openai

from cs.core.config import Settings
from cs.core.db import Database
from cs.core.embeddings import Embedder, cosine
from cs.core.ids import new_id


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


@dataclass(frozen=True)
class Merge:
    slug: str
    into_slug: str
    similarity: float
    reason: str


@dataclass
class MergePlan:
    apply: list[Merge]
    review: list[Merge]
    rejected: list[Verdict]


def _canonical(group: list[str], counts: dict[str, int]) -> str:
    return sorted(group, key=lambda s: (-counts.get(s, 0), len(s), s))[0]


def plan_merges(
    verdicts: list[Verdict], counts: dict[str, int], apply_floor: float = 0.80
) -> MergePlan:
    """Union-find over pairs judged the same concept. A group whose weakest accepted pair is
    under apply_floor goes to review as a whole; everything else is applied."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    accepted = [v for v in verdicts if v.same_concept]
    for v in accepted:
        parent[find(v.pair.a)] = find(v.pair.b)
    groups: dict[str, list[str]] = {}
    for slug in parent:
        groups.setdefault(find(slug), []).append(slug)
    weakest: dict[str, float] = {}
    reasons: dict[str, str] = {}
    for v in accepted:
        root = find(v.pair.a)
        weakest[root] = min(weakest.get(root, 1.0), v.pair.cosine)
        reasons.setdefault(root, v.reason)
    plan = MergePlan(apply=[], review=[], rejected=[v for v in verdicts if not v.same_concept])
    for root, members in groups.items():
        canonical = _canonical(members, counts)
        target = plan.apply if weakest[root] >= apply_floor else plan.review
        for slug in sorted(members):
            if slug != canonical:
                target.append(Merge(slug, canonical, weakest[root], reasons[root]))
    return plan


async def apply_merges(
    db: Database, org_id: UUID, dimension: str, merges: list[Merge], source: str
) -> None:
    if not merges:
        return
    async with db.transaction() as tx:
        for m in merges:
            await tx.execute(
                "UPDATE tag_vocab SET canonical_slug=$4 "
                "WHERE org_id=$1 AND dimension=$2 AND slug=$3",
                org_id,
                dimension,
                m.slug,
                m.into_slug,
            )
            await tx.execute(
                "UPDATE tag SET value=$4 "
                "WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3",
                org_id,
                dimension,
                m.slug,
                m.into_slug,
            )
            await tx.execute(
                "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, "
                "similarity, reason, status, source, decided_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,'applied',$8,now())",
                new_id(),
                org_id,
                dimension,
                m.slug,
                m.into_slug,
                m.similarity,
                m.reason,
                source,
            )
        for canonical in {m.into_slug for m in merges}:
            await tx.execute(
                """
                WITH total AS (
                  SELECT sum(count) AS n FROM tag_vocab
                  WHERE org_id=$1 AND dimension=$2 AND canonical_slug=$3
                )
                UPDATE tag_vocab v SET count =
                  CASE WHEN v.slug=$3 THEN (SELECT n FROM total) ELSE 0 END
                WHERE v.org_id=$1 AND v.dimension=$2 AND v.canonical_slug=$3
                """,
                org_id,
                dimension,
                canonical,
            )


async def record_review(db: Database, org_id: UUID, dimension: str, merges: list[Merge]) -> None:
    for m in merges:
        exists = await db.fetchval(
            "SELECT 1 FROM tag_vocab_merge "
            "WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND into_slug=$4 "
            "AND status='review'",
            org_id,
            dimension,
            m.slug,
            m.into_slug,
        )
        if exists:
            continue
        await db.execute(
            "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, "
            "similarity, reason, status, source) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'review','auto')",
            new_id(),
            org_id,
            dimension,
            m.slug,
            m.into_slug,
            m.similarity,
            m.reason,
        )


async def record_rejections(
    db: Database, org_id: UUID, dimension: str, verdicts: list[Verdict]
) -> None:
    for v in verdicts:
        await db.execute(
            "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, "
            "similarity, reason, status, source, decided_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'rejected','auto',now())",
            new_id(),
            org_id,
            dimension,
            v.pair.a,
            v.pair.b,
            v.pair.cosine,
            v.reason,
        )


async def list_review(db: Database, org_id: UUID) -> list[asyncpg.Record]:
    return await db.fetch(
        "SELECT id, dimension, slug, into_slug, similarity, reason, created_at, status "
        "FROM tag_vocab_merge WHERE org_id=$1 AND status='review' "
        "ORDER BY dimension, similarity DESC, slug",
        org_id,
    )


async def decide_review(db: Database, merge_id: UUID, approve: bool) -> None:
    row = await db.fetchrow(
        "SELECT org_id, dimension, slug, into_slug, similarity, reason FROM tag_vocab_merge "
        "WHERE id=$1 AND status='review'",
        merge_id,
    )
    if row is None:
        raise ValueError(f"no pending review merge {merge_id}")
    if approve:
        # apply_merges writes its own 'applied' log row; mark the review row as decided by
        # turning it into that applied row instead of leaving two.
        await db.execute("DELETE FROM tag_vocab_merge WHERE id=$1", merge_id)
        await apply_merges(
            db,
            row["org_id"],
            row["dimension"],
            [Merge(row["slug"], row["into_slug"], row["similarity"], row["reason"] or "")],
            source="manual",
        )
        await db.execute(
            "UPDATE tag_vocab_merge SET id=$1 "
            "WHERE org_id=$2 AND dimension=$3 AND slug=$4 AND into_slug=$5 "
            "AND status='applied' AND source='manual' AND id <> $1",
            merge_id,
            row["org_id"],
            row["dimension"],
            row["slug"],
            row["into_slug"],
        )
    else:
        await db.execute(
            "UPDATE tag_vocab_merge SET status='rejected', source='manual', decided_at=now() "
            "WHERE id=$1",
            merge_id,
        )


async def revert(db: Database, org_id: UUID, dimension: str, slug: str) -> None:
    """Point `slug` back at itself and restore tag.value from raw_value for the rows whose
    raw slug normalizes to it. Counts are recomputed from the restored rows."""
    from cs.tagging.vocab import normalize

    async with db.transaction() as tx:
        into = await tx.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
        )
        await tx.execute(
            "UPDATE tag_vocab SET canonical_slug=slug WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
        )
        rows = await tx.fetch(
            "SELECT id, raw_value FROM tag "
            "WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3",
            org_id,
            dimension,
            into,
        )
        restored = [r["id"] for r in rows if normalize(r["raw_value"] or "") == slug]
        if restored:
            await tx.execute("UPDATE tag SET value=$2 WHERE id = ANY($1)", restored, slug)
        for s in (slug, into):
            await tx.execute(
                """
                UPDATE tag_vocab SET count = (
                  SELECT count(DISTINCT tag_run_id) FROM tag
                  WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3
                ) WHERE org_id=$1 AND dimension=$2 AND slug=$3
                """,
                org_id,
                dimension,
                s,
            )
        await tx.execute(
            "UPDATE tag_vocab_merge SET status='reverted', decided_at=now() "
            "WHERE org_id=$1 AND dimension=$2 AND slug=$3 AND status='applied'",
            org_id,
            dimension,
            slug,
        )
