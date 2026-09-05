from __future__ import annotations

from datetime import date


def _pct(lift: float) -> str:
    return f"{'+' if lift >= 0 else '-'}{abs(lift) * 100:.0f}%"


def render_brief(week_start: date, patterns, fatigue) -> str:
    lines = [f"# Weekly creative brief — week of {week_start.isoformat()}", ""]
    lines += ["## What is working", ""]
    if not patterns:
        lines.append("No new pattern insights this week.")
    for p in patterns:
        lines.append(f"- **{p['dimension']} = {p['value']}** — {p['metric'].upper()} {_pct(p['lift'])} vs account "
                     f"({p['sample_creatives']} creatives, ${float(p['sample_spend']):.0f} spend)")
        lines.append(f"  - {p['explanation']}")
        lines.append(f"  - Next test: {p['suggested_test']}")
    lines += ["", "## Fatigue alerts", ""]
    if not fatigue:
        lines.append("No fatigue alerts.")
    for f in fatigue:
        lines.append(f"- {f.get('creative_headline') or f['creative_id']} — {f['metric'].upper()} {_pct(f['lift'])} "
                     f"vs its first 14 days. {f['suggested_test']}")
    return "\n".join(lines) + "\n"
