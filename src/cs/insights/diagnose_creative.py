"""Loads a creative's window sums and runs the pure diagnosis over them.

Shared by GET /creatives/{id}/diagnosis and the creative-insight job, so the paragraph is
written against exactly the numbers the panel shows.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from cs.core import repo
from cs.core.db import Database
from cs.insights.diagnosis import (
    FATIGUE_MIN_DAYS,
    PLACEMENT_FLOOR,
    Diagnosis,
    Siblings,
    Sums,
    diagnose,
    length_bucket,
)

# Trailing 30 days ending `until`, matching the library's 30d columns.
WINDOW_DAYS = 30


@dataclass
class DiagnosedCreative:
    diagnosis: Diagnosis
    window: Sums
    baseline: Sums
    first14: Sums | None
    trailing7: Sums | None
    first_day: date | None
    days_live: int | None


def _by_placement(rows: list[Any]) -> dict[tuple[str, str], Sums]:
    return {(r["publisher_platform"], r["platform_position"]): Sums.from_record(r) for r in rows}


def _by_audience(rows: list[Any]) -> dict[tuple[str, str], int]:
    return {(r["age"], r["gender"]): int(r["impressions"] or 0) for r in rows}


async def _siblings(
    db: Database,
    org_id: UUID,
    account_id: UUID,
    creative_id: UUID | list[UUID],
    fmt: str,
    since: date,
    until: date,
) -> tuple[Siblings, list[UUID], dict[tuple[str, str], Sums]]:
    """The narrowest peer group that can actually support a comparison, and its placement sums.

    Narrow is better -- same ad set means the same targeting, so the optimiser's allocation is
    the only thing left that differs -- but a handful of peers with a few hundred impressions
    cannot clear the floor fit needs. So each scope is judged on the delivery it brings, not on
    how many creatives it contains, and the walk stops at the first one that can answer."""
    widest: tuple[Siblings, list[UUID], dict[tuple[str, str], Sums]] | None = None
    for scope in repo.SIBLING_SCOPES:
        ids = await repo.sibling_creative_ids(
            db, org_id, account_id, creative_id, fmt, since, until, scope
        )
        sums = _by_placement(await repo.sibling_placement_sums(db, org_id, ids, since, until))
        widest = (Siblings(scope, len(ids)), ids, sums)  # type: ignore[arg-type]
        comparable = [s for s in sums.values() if s.impressions >= PLACEMENT_FLOOR]
        if len(ids) >= repo.MIN_SIBLINGS and len(comparable) >= 2:
            return widest
    assert widest is not None
    return widest


async def diagnose_creative(
    db: Database,
    org_id: UUID,
    creative: Mapping[str, Any],
    until: date,
    duration_ms: int | None = None,
    member_ids: list[UUID] | None = None,
) -> DiagnosedCreative:
    """With `member_ids` (the creative's footage group, see cs.core.repo.footage) every sum
    pools the group and every baseline and peer set leaves the whole group out. One video
    copied into six campaigns is one piece of footage: read copy by copy, each is too thin to
    measure and the others would sit in its baseline as if they were different ads."""
    creative_id: UUID | list[UUID] = member_ids or creative["id"]
    since = until - timedelta(days=WINDOW_DAYS - 1)
    window = Sums.from_record(
        await repo.creative_window_sums(db, org_id, creative_id, since, until)
    )
    base = await repo.baseline_window_sums(
        db, org_id, creative["ad_account_id"], creative["format"], creative_id, since, until
    )
    first_day = await repo.creative_first_metric_day(db, org_id, creative_id)
    days_live = (until - first_day).days + 1 if first_day else None
    first14 = trailing7 = None
    if first_day is not None and days_live is not None and days_live >= FATIGUE_MIN_DAYS:
        first14 = Sums.from_record(
            await repo.creative_window_sums(
                db, org_id, creative_id, first_day, first_day + timedelta(days=13)
            )
        )
        trailing7 = Sums.from_record(
            await repo.creative_window_sums(
                db, org_id, creative_id, until - timedelta(days=6), until
            )
        )
    baseline = Sums.from_record(base)
    if duration_ms is None:
        duration_ms = creative["primary_asset_duration_ms"]
    retention_baseline, retention_creatives = None, 0
    bucket = length_bucket(duration_ms) if creative["format"] == "video" else None
    if bucket is not None:
        lo, hi = bucket
        same_length = await repo.baseline_window_sums(
            db,
            org_id,
            creative["ad_account_id"],
            "video",
            creative_id,
            since,
            until,
            (lo * 1000, hi * 1000 if hi is not None else None),
        )
        retention_baseline = Sums.from_record(same_length)
        retention_creatives = int(same_length["creatives"] or 0)
    account_id, fmt = creative["ad_account_id"], creative["format"]
    placements = _by_placement(
        await repo.creative_placement_sums(db, org_id, creative_id, since, until)
    )
    siblings, sibling_ids, placement_siblings = await _siblings(
        db, org_id, account_id, creative_id, fmt, since, until
    )
    audience = _by_audience(
        await repo.creative_audience_sums(db, org_id, creative_id, since, until)
    )
    audience_siblings = _by_audience(
        await repo.sibling_audience_sums(db, org_id, sibling_ids, since, until)
    )
    diagnosis = diagnose(
        fmt=creative["format"],
        window=window,
        baseline=baseline,
        baseline_creatives=int(base["creatives"] or 0),
        first14=first14,
        trailing7=trailing7,
        days_live=days_live,
        duration_ms=duration_ms,
        window_start=since,
        window_end=until,
        retention_baseline=retention_baseline,
        retention_baseline_creatives=retention_creatives,
        placements=placements,
        placement_siblings=placement_siblings,
        siblings=siblings,
        audience=audience,
        audience_siblings=audience_siblings,
    )
    return DiagnosedCreative(diagnosis, window, baseline, first14, trailing7, first_day, days_live)
