"""Merge near-synonym open-vocabulary slugs (spec 2026-09-16-vocab-merge-design.md).

Embeddings find candidate pairs; a text-only judge decides; a union-find plan applies
or routes to review; every decision is logged in tag_vocab_merge and reversible."""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
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
    # False when the judge returned no entry for this pair. Such a verdict is not a
    # decision: it must never be persisted as a rejection, which is permanent.
    answered: bool = True


class Judge(Protocol):
    async def judge(
        self, dimension: str, description: str, pairs: list[Pair], context: dict[str, SlugContext]
    ) -> list[Verdict]: ...


JUDGE_SYSTEM = (
    "You decide whether two short tags from an ad-creative taxonomy name the same concept "
    "for the purpose of grouping ads. Same concept means a strategist would treat the two "
    "as interchangeable labels, not merely related. An ingredient and a product containing "
    "it are different. A brand and a category are different. A claim and its negation are "
    "different. A specific value and its general category are different. "
    "A claim about falling asleep and a claim about sleep quality or waking rested are "
    "different claims. "
    "A generic ingredient and a branded or specific form of it (an extract, a salt, a "
    "delivery system) are different. "
    "When in doubt, answer false. Answer every pair."
)

# One request per 50 pairs: a dimension can produce hundreds of candidates, and a single
# unbounded call both risks a truncated response and loses every verdict when it fails.
JUDGE_BATCH = 50


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


def _key(a: str, b: str) -> tuple[str, str]:
    """Order- and case-insensitive pair key: the model echoes slugs back, sometimes
    swapped or recased, and a near-miss there must not silently lose the verdict."""
    return tuple(sorted((a.strip().lower(), b.strip().lower())))  # type: ignore[return-value]


class GatewayJudge:
    def __init__(self, settings: Settings, client: openai.AsyncOpenAI | None = None):
        self.client = client or openai.AsyncOpenAI(
            api_key=settings.ai_gateway_api_key, base_url=f"{settings.ai_gateway_base_url}/v1"
        )
        self.model = settings.judge_model
        # (prompt_tokens, completion_tokens) per call, for the run's cost line.
        self.usage: list[tuple[int, int]] = []

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
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.usage.append(
                (
                    getattr(usage, "prompt_tokens", 0) or 0,
                    getattr(usage, "completion_tokens", 0) or 0,
                )
            )
        choice = resp.choices[0]
        # A refused or truncated response carries no verdicts. Synthesising rejections from
        # it would write permanent 'rejected' rows the next run never re-proposes, so the
        # caller has to see the failure instead (mirrors GeminiGatewayTagger.tag).
        if choice.finish_reason == "content_filter" or getattr(choice.message, "refusal", None):
            raise RuntimeError("judge refused")
        if choice.finish_reason == "length":
            raise RuntimeError("judge response truncated (finish_reason=length)")
        text = choice.message.content or "{}"
        answered: dict[tuple[str, str], tuple[bool, str]] = {}
        for item in json.loads(text).get("pairs", []):
            key = _key(str(item.get("a", "")), str(item.get("b", "")))
            answered[key] = (bool(item.get("same_concept")), str(item.get("reason", "")))
        out: list[Verdict] = []
        for p in pairs:
            hit = answered.get(_key(p.a, p.b))
            if hit is None:
                out.append(Verdict(p, False, "no verdict returned", answered=False))
            else:
                out.append(Verdict(p, hit[0], hit[1]))
        return out


async def judge_in_batches(
    judge: Judge,
    dimension: str,
    description: str,
    pairs: list[Pair],
    context: dict[str, SlugContext],
    batch: int = JUDGE_BATCH,
) -> list[Verdict]:
    """Judge `pairs` in fixed-size chunks, concatenating the verdicts in pair order."""
    out: list[Verdict] = []
    for i in range(0, len(pairs), batch):
        out.extend(await judge.judge(dimension, description, pairs[i : i + batch], context))
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
    # Pairs the judge returned no verdict for: neither merged nor rejected, so the next
    # run proposes them again.
    unanswered: list[Verdict] = field(default_factory=list)


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
    best: dict[str, tuple[float, str]] = {}
    for v in accepted:
        root = find(v.pair.a)
        weakest[root] = min(weakest.get(root, 1.0), v.pair.cosine)
        reasons.setdefault(root, v.reason)
        for slug in (v.pair.a, v.pair.b):
            if slug not in best or v.pair.cosine > best[slug][0]:
                best[slug] = (v.pair.cosine, v.reason)
    plan = MergePlan(
        apply=[],
        review=[],
        rejected=[v for v in verdicts if v.answered and not v.same_concept],
        unanswered=[v for v in verdicts if not v.answered],
    )
    for root, members in groups.items():
        canonical = _canonical(members, counts)
        # The group is routed by its weakest accepted pair, but each member logs the
        # strongest accepted pair it took part in, so the row carries its own evidence.
        target = plan.apply if weakest[root] >= apply_floor else plan.review
        for slug in sorted(members):
            if slug != canonical:
                similarity, reason = best.get(slug, (weakest[root], reasons[root]))
                target.append(Merge(slug, canonical, similarity, reason))
    return plan


async def _apply(tx: Database, org_id: UUID, dimension: str, merges: list[Merge]) -> None:
    """Repoint each member at its canonical, rewrite open tag values and recompute counts.
    Writes no log rows, and must be called inside a transaction."""
    canonicals = {m.into_slug for m in merges}
    for canonical in sorted(canonicals):
        # The target must exist AND still be its own canonical: merging into a slug that
        # was itself merged away would leave a two-hop chain that hints() and the count
        # recomputation (both keyed on canonical_slug) silently drop.
        known = await tx.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            canonical,
        )
        if known is None:
            raise ValueError(f"canonical {canonical!r} is not in the vocabulary")
        if known != canonical:
            raise ValueError(f"canonical {canonical!r} is not canonical")
    for m in merges:
        await tx.execute(
            "UPDATE tag_vocab SET canonical_slug=$4 WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            m.slug,
            m.into_slug,
        )
        await tx.execute(
            "UPDATE tag SET value=$4 WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3",
            org_id,
            dimension,
            m.slug,
            m.into_slug,
        )
    # count is the number of distinct runs carrying the slug -- what bump_counts
    # accumulates -- so it is recounted from the rewritten rows, never summed across
    # members (one run carrying two members of the group would be counted twice).
    for canonical in sorted(canonicals):
        await tx.execute(
            """
            UPDATE tag_vocab SET count = CASE WHEN slug=$3 THEN (
              SELECT count(DISTINCT tag_run_id) FROM tag
              WHERE org_id=$1 AND dimension=$2 AND kind='open' AND value=$3
            ) ELSE 0 END
            WHERE org_id=$1 AND dimension=$2 AND canonical_slug=$3
            """,
            org_id,
            dimension,
            canonical,
        )


async def apply_merges(
    db: Database, org_id: UUID, dimension: str, merges: list[Merge], source: str
) -> None:
    if not merges:
        return
    async with db.transaction() as tx:
        await _apply(tx, org_id, dimension, merges)
        for m in merges:
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
    # Only decisions are persisted: a 'rejected' row is permanent (rejected_pairs never
    # proposes the pair again), and an unanswered pair is the absence of a decision.
    for v in verdicts:
        if not v.answered:
            continue
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
    """Approve or reject one pending review row. The row itself becomes the decision, so
    an approved merge and its log entry commit together or not at all."""
    async with db.transaction() as tx:
        row = await tx.fetchrow(
            "SELECT org_id, dimension, slug, into_slug, similarity, reason FROM tag_vocab_merge "
            "WHERE id=$1 AND status='review' FOR UPDATE",
            merge_id,
        )
        if row is None:
            raise ValueError(f"no pending review merge {merge_id}")
        if approve:
            # The row may have sat in review while its target was merged elsewhere, so the
            # target is resolved to its current canonical inside this transaction. Merging
            # into the stale target would build a chain the count/hints queries drop.
            target = await tx.fetchval(
                "SELECT canonical_slug FROM tag_vocab "
                "WHERE org_id=$1 AND dimension=$2 AND slug=$3 FOR UPDATE",
                row["org_id"],
                row["dimension"],
                row["into_slug"],
            )
            if target == row["slug"]:
                # The reverse merge already happened: approving would point the slug at
                # itself. Record it as decided rather than corrupting the chain.
                await tx.execute(
                    "UPDATE tag_vocab_merge SET status='rejected', source='manual', "
                    "reason='target merged into this slug', decided_at=now() WHERE id=$1",
                    merge_id,
                )
                return
            # An unknown target keeps the stale slug so _apply raises the vocabulary error.
            target = target or row["into_slug"]
            await _apply(
                tx,
                row["org_id"],
                row["dimension"],
                [Merge(row["slug"], target, row["similarity"], row["reason"] or "")],
            )
            await tx.execute(
                "UPDATE tag_vocab_merge SET status='applied', source='manual', into_slug=$2, "
                "decided_at=now() WHERE id=$1",
                merge_id,
                target,
            )
        else:
            await tx.execute(
                "UPDATE tag_vocab_merge SET status='rejected', source='manual', decided_at=now() "
                "WHERE id=$1",
                merge_id,
            )


async def revert(db: Database, org_id: UUID, dimension: str, slug: str) -> None:
    """Point `slug` back at itself and restore tag.value from raw_value for the rows whose
    raw slug normalizes to it. Counts are recomputed from the restored rows, and the pair
    is logged as rejected so the next run does not propose it again. Raises ValueError for
    an unknown slug; does nothing for a slug that is not merged."""
    from cs.tagging.vocab import normalize

    async with db.transaction() as tx:
        into = await tx.fetchval(
            "SELECT canonical_slug FROM tag_vocab WHERE org_id=$1 AND dimension=$2 AND slug=$3",
            org_id,
            dimension,
            slug,
        )
        if into is None:
            raise ValueError(f"no vocab row for {slug!r}")
        if into == slug:
            return
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
        # A reverted pair must not be proposed again: rejected_pairs only reads
        # status='rejected', so record the operator's decision as one.
        await tx.execute(
            "INSERT INTO tag_vocab_merge (id, org_id, dimension, slug, into_slug, "
            "similarity, reason, status, source, decided_at) "
            "VALUES ($1,$2,$3,$4,$5,0.0,'reverted by operator','rejected','manual',now())",
            new_id(),
            org_id,
            dimension,
            slug,
            into,
        )
