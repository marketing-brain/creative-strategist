"""Blind grouping for an experiment: embed one description answer per file, cluster the
vectors, then have a model name each group from a sample of its answers.

Nothing here sees a file's label. The number of groups is chosen by silhouette (how much
closer each file sits to its own group than to the next one), never by how well the groups
match the labels, so the score step judges a grouping the labels could not have shaped.

Pure Python on purpose (the project carries no numpy): a few hundred files are a few
seconds of arithmetic.
"""

from __future__ import annotations

import hashlib
import json
import math
import operator
from dataclasses import dataclass
from uuid import UUID

from cs.core import model_calls
from cs.core.db import Database

EMBED_PURPOSE = "experiments.embed"
NAME_PURPOSE = "experiments.name_group"
K_RANGE = range(4, 21)  # candidate group counts; silhouette picks one
NAME_SAMPLES = 10


# ---------------------------------------------------------------- embeddings


async def stored_vectors(
    db: Database, ids: list[UUID], prompt_version: str, field: str, model: str
) -> dict[UUID, list[float]]:
    rows = await db.fetch(
        "SELECT footage_id, vector FROM footage_description_embedding"
        " WHERE footage_id = ANY($1::uuid[]) AND prompt_version=$2 AND field=$3 AND model=$4",
        ids,
        prompt_version,
        field,
        model,
    )
    return {r["footage_id"]: list(r["vector"]) for r in rows}


async def embed_missing(
    db: Database,
    embedder,
    org_id: UUID,
    texts: dict[UUID, str],
    prompt_version: str,
    field: str,
) -> int:
    """Embed the answers not embedded yet, in one ledgered request. Returns how many."""
    model = embedder.model
    have = await stored_vectors(db, list(texts), prompt_version, field, model)
    todo = [fid for fid in texts if fid not in have]
    if not todo:
        return 0
    key = hashlib.sha256(
        f"{prompt_version}:{field}:{','.join(sorted(map(str, todo)))}".encode()
    ).hexdigest()[:32]
    claim = await model_calls.claim(db, org_id, EMBED_PURPOSE, key, model)
    try:
        vectors = await embedder.embed([texts[f] for f in todo])
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    await model_calls.finish(db, claim.id, input_tokens=sum(len(texts[f]) // 4 for f in todo))
    await db.executemany(
        "INSERT INTO footage_description_embedding (footage_id, prompt_version, field, model, vector)"
        " VALUES ($1, $2, $3, $4, $5) ON CONFLICT DO NOTHING",
        [(f, prompt_version, field, model, v) for f, v in zip(todo, vectors, strict=True)],
    )
    return len(todo)


# ---------------------------------------------------------------- clustering


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def distances(vectors: list[list[float]]) -> list[list[float]]:
    """Cosine distance (1 - cosine similarity) between every pair."""
    units = [_unit(v) for v in vectors]
    n = len(units)
    d = [[0.0] * n for _ in range(n)]
    for i in range(n):
        ui = units[i]
        for j in range(i + 1, n):
            x = 1.0 - sum(map(operator.mul, ui, units[j]))
            d[i][j] = d[j][i] = x
    return d


def average_linkage(d: list[list[float]]) -> list[tuple[int, int]]:
    """Agglomerative clustering, average linkage. Returns the merges in order as pairs of
    cluster ids (0..n-1 are the files; each merge creates id n, n+1, ...)."""
    n = len(d)
    size = {i: 1 for i in range(n)}
    dist = {i: {j: d[i][j] for j in range(n) if j != i} for i in range(n)}
    merges: list[tuple[int, int]] = []
    nxt = n
    while len(size) > 1:
        a, b, best = -1, -1, math.inf
        for i, row in dist.items():
            for j, x in row.items():
                if i < j and x < best:
                    a, b, best = i, j, x
        merges.append((a, b))
        na, nb = size.pop(a), size.pop(b)
        new = {}
        for k in size:
            # Lance-Williams update for average linkage.
            new[k] = (na * dist[a][k] + nb * dist[b][k]) / (na + nb)
        for k in size:
            dist[k].pop(a, None)
            dist[k].pop(b, None)
            dist[k][nxt] = new[k]
        dist.pop(a)
        dist.pop(b)
        dist[nxt] = new
        size[nxt] = na + nb
        nxt += 1
    return merges


def ward_linkage(d: list[list[float]]) -> list[tuple[int, int]]:
    """Agglomerative clustering, Ward linkage: each merge is the one that adds least spread
    inside groups, so groups come out compact and of similar size. Average linkage, the first
    choice, chained: on org 3's format answers it left 237 of 247 files in one group. Works
    on squared Euclidean distance, which for unit vectors is 2 x the cosine distance."""
    n = len(d)
    size = {i: 1 for i in range(n)}
    dist = {i: {j: 2.0 * d[i][j] for j in range(n) if j != i} for i in range(n)}
    merges: list[tuple[int, int]] = []
    nxt = n
    while len(size) > 1:
        a, b, best = -1, -1, math.inf
        for i, row in dist.items():
            for j, x in row.items():
                if i < j and x < best:
                    a, b, best = i, j, x
        merges.append((a, b))
        na, nb = size.pop(a), size.pop(b)
        dab = dist[a][b]
        new = {}
        for k, nk in size.items():
            # Lance-Williams update for Ward.
            new[k] = ((na + nk) * dist[a][k] + (nb + nk) * dist[b][k] - nk * dab) / (na + nb + nk)
        for k in size:
            dist[k].pop(a, None)
            dist[k].pop(b, None)
            dist[k][nxt] = new[k]
        dist.pop(a)
        dist.pop(b)
        dist[nxt] = new
        size[nxt] = na + nb
        nxt += 1
    return merges


def cut(n: int, merges: list[tuple[int, int]], k: int) -> list[int]:
    """Group index per file when the merge history stops at k groups."""
    parent = list(range(2 * n))
    for step, (a, b) in enumerate(merges[: n - k]):
        parent[a] = parent[b] = n + step

    def root(x: int) -> int:
        while parent[x] != x:
            x = parent[x]
        return x

    roots = [root(i) for i in range(n)]
    order = {r: i for i, r in enumerate(dict.fromkeys(roots))}
    return [order[r] for r in roots]


def silhouette(d: list[list[float]], labels: list[int]) -> float:
    n = len(labels)
    groups: dict[int, list[int]] = {}
    for i, g in enumerate(labels):
        groups.setdefault(g, []).append(i)
    total = 0.0
    for i in range(n):
        own = groups[labels[i]]
        if len(own) == 1:
            continue  # a singleton scores 0 by convention
        a = sum(d[i][j] for j in own if j != i) / (len(own) - 1)
        b = min(sum(d[i][j] for j in m) / len(m) for g, m in groups.items() if g != labels[i])
        total += (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return total / n


@dataclass
class Grouping:
    k: int
    labels: list[int]
    scores: dict[int, float]  # silhouette per candidate k


def group(vectors: list[list[float]], k: int | None = None) -> Grouping:
    d = distances(vectors)
    merges = ward_linkage(d)
    n = len(vectors)
    scores = {c: silhouette(d, cut(n, merges, c)) for c in K_RANGE if c < n}
    chosen = k or max(scores, key=lambda c: (scores[c], -c))
    return Grouping(chosen, cut(n, merges, chosen), scores)


# ---------------------------------------------------------------- naming

NAME_SYSTEM = """You name groups of ads for a creative strategist. Each group was formed \
automatically because the descriptions of how its ads are made read alike. You see a sample \
of those descriptions. Name what they share in how the ad is made (the kind of footage or \
artwork, how it is produced and edited), not what the ads say. Use plain words a strategist \
would use. If the sample does not share one clear way of being made, say so in the \
definition rather than forcing a name."""

NAME_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "description": "Two to five words."},
        "definition": {
            "type": "string",
            "description": "One sentence: what the ads in this group have in common in how "
            "they are made.",
        },
    },
    "required": ["name", "definition"],
}


async def name_group(
    db: Database, client, model: str, org_id: UUID, run: str, ordinal: int, samples: list[str]
) -> tuple[dict, int, int]:
    """One ledgered call. Returns ({name, definition}, input tokens, output tokens)."""
    claim = await model_calls.claim(db, org_id, NAME_PURPOSE, f"{run}:{ordinal}", model)
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[
                {"role": "system", "content": NAME_SYSTEM},
                {
                    "role": "user",
                    "content": "Descriptions of how each ad in the group is made:\n"
                    + "\n".join(f"- {s}" for s in samples),
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "group", "schema": NAME_SCHEMA, "strict": True},
            },
        )
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("model response truncated")
        out = json.loads(choice.message.content or "{}")
    except Exception as exc:
        await model_calls.fail(db, claim.id, str(exc))
        raise
    usage = getattr(resp, "usage", None)
    tin = getattr(usage, "prompt_tokens", 0) or 0
    tout = getattr(usage, "completion_tokens", 0) or 0
    await model_calls.finish(db, claim.id, input_tokens=tin, output_tokens=tout)
    return out, tin, tout
