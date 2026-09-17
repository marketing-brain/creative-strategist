from __future__ import annotations

from datetime import date


def _pct(lift: float) -> str:
    return f"{'+' if lift >= 0 else '-'}{abs(lift) * 100:.0f}%"


def render_brief(week_start: date, fatigue) -> str:
    lines = [f"# Weekly creative brief — week of {week_start.isoformat()}", ""]
    lines += ["## Fatigue alerts", ""]
    if not fatigue:
        lines.append("No fatigue alerts.")
    for f in fatigue:
        lines.append(
            f"- {f.get('creative_headline') or f['creative_id']} — {f['metric'].upper()} "
            f"{_pct(f['lift'])} vs its first 14 days. {f['suggested_test']}"
        )
    return "\n".join(lines) + "\n"
