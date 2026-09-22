"""Does what happens on screen move the exit rate, measured inside each video?

The account read compares whole videos against each other, so every difference between the
files -- audience, campaign, period, offer, everything the optimiser decided -- rides along
with the attribute being tested. Here each video is compared with itself: the question is
whether the seconds in which something happens on screen lose viewers faster than the other
seconds of the same video.

Two things are held constant at once, because both matter. A video's own drop-off level is
absorbed (a video everybody leaves is not evidence against whatever happens at second 12),
and so is the position in the curve (every video loses viewers fastest at the start, so an
early cut must be judged against other videos' early seconds). What is left is the part a
between-video comparison cannot see.

Pure Python: no numpy, matching cs.insights.account_read.stats.
"""

from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cs.insights.event_study.types import Interval

MIN_PLAYS = 1_000  # below this Meta's whole-percent rounding moves a curve point visibly
MIN_FILES = 8
DEMEAN_PASSES = 20


@dataclass(frozen=True)
class Effect:
    """Change in exit rate (viewers lost per second) per unit of the treatment."""

    name: str
    estimate: float
    ci_low: float
    ci_high: float
    n_obs: int
    n_files: int
    n_treated_obs: int
    treated_mean: float  # raw exit rate where the treatment is present
    control_mean: float  # raw exit rate where it is absent

    @property
    def crosses_zero(self) -> bool:
        return self.ci_low <= 0 <= self.ci_high


def group_members(groups: Sequence[Sequence[int]]) -> list[list[list[int]]]:
    """Row indices per group, built once and reused by every demeaning pass."""
    out: list[list[list[int]]] = []
    for grouping in groups:
        by_key: dict[int, list[int]] = {}
        for i, g in enumerate(grouping):
            by_key.setdefault(g, []).append(i)
        out.append(list(by_key.values()))
    return out


def _twoway_demean(
    y: list[float], members: list[list[list[int]]], passes: int = DEMEAN_PASSES
) -> list[float]:
    """Subtract each group's mean, alternating between the groupings until it settles.

    With one grouping this is a single pass. With two (the file and the position in the
    curve) the means shift each other, so the passes repeat; the panels here are small and
    nearly balanced, and it converges in a handful.
    """
    out = list(y)
    scale = max((abs(v) for v in out), default=0.0) or 1.0
    for _ in range(passes):
        moved = 0.0
        for grouping in members:
            for idx in grouping:
                m = sum(out[i] for i in idx) / len(idx)
                if m:
                    moved = max(moved, abs(m))
                    for i in idx:
                        out[i] -= m
        if moved < 1e-9 * scale:
            break
    return out


def _slope(x: list[float], y: list[float]) -> float | None:
    sxx = sum(v * v for v in x)
    if sxx <= 1e-12:
        return None
    return sum(a * b for a, b in zip(x, y, strict=True)) / sxx


def _fit(
    rows: list[Interval],
    treatment: Callable[[Interval], float],
    file_keys: Sequence[object] | None = None,
) -> float | None:
    """The treatment's slope on the exit rate, with the file and the curve position taken
    out of both sides first (Frisch-Waugh: regressing the residuals gives the same
    coefficient as including a term per file and per position).

    `file_keys` names the file each row belongs to when that is not the footage id -- a
    bootstrap draw needs one key per pick, so a file drawn twice counts as two files.
    """
    keys = list(file_keys) if file_keys is not None else [r.footage_id for r in rows]
    if len(set(keys)) < 2:
        return None
    index = {f: i for i, f in enumerate(dict.fromkeys(keys))}
    by_file = [index[k] for k in keys]
    members = group_members((by_file, [r.bucket for r in rows]))
    y = _twoway_demean([r.exit_rate for r in rows], members)
    x = _twoway_demean([treatment(r) for r in rows], members)
    return _slope(x, y)


def fit_effect(
    rows: list[Interval],
    treatment: Callable[[Interval], float],
    *,
    name: str,
    draws: int = 500,
    seed: int = 7,
    ci: float = 0.95,
) -> Effect | None:
    """The effect with a confidence interval from resampling whole files.

    Files are resampled, not intervals: a video's fifteen intervals are one video's
    behaviour, and treating them as fifteen independent facts would make every interval
    look far more certain than it is.
    """
    by_file: dict = {}
    for r in rows:
        by_file.setdefault(r.footage_id, []).append(r)
    if len(by_file) < MIN_FILES:
        return None
    point = _fit(rows, treatment)
    if point is None:
        return None
    keys = list(by_file)
    rng = random.Random(seed)
    estimates: list[float] = []
    for _ in range(draws):
        sample: list[Interval] = []
        sample_keys: list[object] = []
        # A file drawn twice counts as two files, or the two copies collapse into one fixed
        # effect and the draw understates how much a single file moves the estimate.
        for n, k in enumerate(rng.choices(keys, k=len(keys))):
            sample += by_file[k]
            sample_keys += [(k, n)] * len(by_file[k])
        est = _fit(sample, treatment, sample_keys)
        if est is not None:
            estimates.append(est)
    if len(estimates) < draws // 2:
        return None
    estimates.sort()
    lo = estimates[int((1 - ci) / 2 * len(estimates))]
    hi = estimates[min(len(estimates) - 1, int((1 + ci) / 2 * len(estimates)))]
    treated = [r.exit_rate for r in rows if treatment(r) > 0]
    control = [r.exit_rate for r in rows if treatment(r) == 0]
    return Effect(
        name=name,
        estimate=point,
        ci_low=lo,
        ci_high=hi,
        n_obs=len(rows),
        n_files=len(by_file),
        n_treated_obs=len(treated),
        treated_mean=statistics.fmean(treated) if treated else math.nan,
        control_mean=statistics.fmean(control) if control else math.nan,
    )


def usable(rows: list[Interval], *, fine_only: bool = False) -> list[Interval]:
    """Intervals worth reading: enough plays that the curve is not rounding noise, the
    whole interval inside the video, and a curve that has not already emptied out."""
    out = [
        r
        for r in rows
        if r.plays >= MIN_PLAYS
        and r.end_s <= r.duration_s
        and r.share_from > 0.01
        and r.share_to > 0
    ]
    return [r for r in out if r.seconds <= 1] if fine_only else out
