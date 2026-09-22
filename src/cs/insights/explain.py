"""Template text for fatigue insights. No model call: the numbers say everything."""

from __future__ import annotations

from cs.insights.types import InsightDraft


def _template(d: InsightDraft) -> tuple[str, str]:
    pct = f"{abs(d.lift) * 100:.0f}%"
    return (
        f"This creative's {d.metric.upper()} is {pct} worse over the last 7 days than in "
        "the 14 days before them.",
        "Refresh the hook and first frame while keeping the winning offer and message.",
    )
