"""How far the tagger agrees with itself, measured on files it tagged more than once.

Meta mints a new creative for every copied ad, so one video file is often tagged several
times from scratch. Those runs saw the same pixels, and where they differ the label is noise:
nine runs of one file split six to three on `opening_shot` while agreeing on every scene flag
underneath. An attribute the tagger flips on cannot carry the claim that one ad has or lacks
it.

The tagger's own confidence is no substitute. It reported 1.0 on both sides of that split."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from itertools import combinations
from uuid import UUID

from cs.core.db import Database
from cs.insights.account_read.facets import NUMERIC_DIMS, Extracted, extract

FLAG = "tagger_disagreement"  # rides in Test.confounds
MIN_DIM_FILES = 10  # re-tagged files a dimension needs before its score stands in
# Agreement is "both runs say it" over "either run says it". A tagger that spots a present
# trait in nine runs of ten scores exactly 0.8; missing it in one run of five scores 0.6.
MIN_AGREEMENT = 0.8
NUMERIC_TOLERANCE = 0.2  # two readings of a number agree within this share of the larger

# Copied runs (same file, same copy text) record no tokens and would count as free agreement.
# Scoped to one org: open dimensions are canonicalised against that org's own vocabulary, so
# the same slug in two orgs is not the same population.
_RUNS = """
WITH latest AS (
  SELECT DISTINCT ON (r.creative_id) r.creative_id, r.id AS run_id
  FROM tag_run r JOIN taxonomy_version v ON v.id = r.taxonomy_version_id
  WHERE r.org_id = $1 AND r.status = 'done' AND split_part(v.version, '.', 1)::int >= 2
    AND coalesce(r.input_tokens, 0) > 0
  ORDER BY r.creative_id, r.finished_at DESC
), runs AS (
  SELECT c.ad_account_id, a.sha256, a.duration_ms, l.run_id,
         count(*) OVER (PARTITION BY c.ad_account_id, a.sha256) AS n
  FROM latest l
  JOIN creative c ON c.id = l.creative_id
  JOIN asset a ON a.id = c.primary_asset_id
  WHERE c.org_id = $1 AND c.format = 'video' AND a.kind = 'original_video'
)
SELECT r.ad_account_id, r.sha256, r.duration_ms, r.run_id,
       t.dimension, t.kind, t.value, t.raw_value, t.t_start_s, t.ordinal
FROM runs r JOIN tag t ON t.tag_run_id = r.run_id
WHERE r.n > 1 AND t.kind <> 'text'
"""


@dataclass(frozen=True)
class Score:
    agreement: float
    files: int  # re-tagged files this was averaged over, never run pairs


@dataclass
class Reliability:
    """Scores per (dimension, value), and per dimension pooled over its values.

    A value that was measured answers with its own score, however few files it stood on: a
    dimension's mean is a bad stand-in for a value that has been tried and missed, and it
    carried `messenger_age_band = 40_to_60` (0.50 on four files) into a test to run. Only a
    value never seen on a re-tagged file falls back to its dimension. Nothing measured at
    either level passes, because an account with no re-tagged files has nothing to judge by.

    The errors are not symmetric. A wrongly flagged attribute costs one claim about one ad;
    a wrongly trusted one puts a false claim on that ad's page."""

    values: dict[tuple[str, str], Score] = field(default_factory=dict)
    dimensions: dict[str, Score] = field(default_factory=dict)

    def score(self, dimension: str, value: str) -> Score | None:
        v = self.values.get((dimension, value))
        if v is not None:
            return v
        d = self.dimensions.get(dimension)
        return d if d is not None and d.files >= MIN_DIM_FILES else None

    def reliable(self, dimension: str, value: str) -> bool:
        s = self.score(dimension, value)
        return s is None or s.agreement >= MIN_AGREEMENT


def _numeric(runs: list[Extracted], dim: str) -> float | None:
    pairs = [
        (a.numeric[dim], b.numeric[dim])
        for a, b in combinations(runs, 2)
        if dim in a.numeric and dim in b.numeric
    ]
    if not pairs:
        return None
    ok = [abs(x - y) <= NUMERIC_TOLERANCE * max(abs(x), abs(y), 1e-9) for x, y in pairs]
    return sum(ok) / len(ok)


def agreement(files: list[list[Extracted]]) -> Reliability:
    """Per file and value: of the run pairs where either run says it, the share where both do.
    Averaged over files, so one file tagged nine times counts once. A dimension's score is the
    same thing averaged over its values within a file first, so it too is a per-file mean. A
    numeric dimension is scored on the raw number; its tercile labels belong to one account's
    bin edges and say nothing about the tagger."""
    per_value: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_dim: dict[str, list[float]] = defaultdict(list)
    for runs in files:
        in_file: dict[str, list[float]] = defaultdict(list)
        for f in {f for x in runs for f in x.facets}:
            pairs = [(f in a.facets, f in b.facets) for a, b in combinations(runs, 2)]
            pairs = [p for p in pairs if any(p)]
            share = sum(all(p) for p in pairs) / len(pairs)
            per_value[f].append(share)
            in_file[f[0]].append(share)
        for dim in NUMERIC_DIMS:
            if (share := _numeric(runs, dim)) is not None:
                in_file[dim].append(share)
        for dim, shares in in_file.items():
            per_dim[dim].append(statistics.fmean(shares))

    def score(v: list[float]) -> Score:
        return Score(round(statistics.fmean(v), 3), len(v))

    return Reliability(
        values={f: score(v) for f, v in sorted(per_value.items())},
        dimensions={d: score(v) for d, v in sorted(per_dim.items())},
    )


async def measure(db: Database, org_id: UUID) -> Reliability:
    by_run: dict[tuple, list[dict]] = defaultdict(list)
    duration: dict[tuple, float | None] = {}
    for r in await db.fetch(_RUNS, org_id):
        key = (r["ad_account_id"], r["sha256"], r["run_id"])
        by_run[key].append(dict(r))
        duration[key] = r["duration_ms"] / 1000.0 if r["duration_ms"] else None
    by_file: dict[tuple, list[Extracted]] = defaultdict(list)
    for key, rows in by_run.items():
        by_file[key[:2]].append(extract(rows, duration[key]))
    return agreement([runs for runs in by_file.values() if len(runs) > 1])
