"""Tag rows of one creative's v2 run → facets for the account read."""

from __future__ import annotations

from dataclasses import dataclass, field

SKIP_DIMS = {"beats", "transcript", "hook_verbatim", "concept_summary", "overlay_texts", "scenes"}
TEXT_DIMS = ("concept_summary", "hook_verbatim")
NUMERIC_DIMS = ("duration_s", "cuts_per_10s")
DERIVED_SINGLE_DIMS = ("first_product_shown", "opening_scene")
REVEAL_BINS = ("under 5s", "5 to 10s", "10s to midpoint", "second half", "never")


@dataclass
class Extracted:
    facets: set[tuple[str, str]] = field(default_factory=set)
    numeric: dict[str, float] = field(default_factory=dict)
    first_product_s: float | None = None
    concept_summary: str | None = None
    hook_verbatim: str | None = None


def reveal_bin(t0: float | None, duration_s: float | None) -> str:
    if t0 is None:
        return "never"
    if t0 < 5:
        return "under 5s"
    if t0 < 10:
        return "5 to 10s"
    if duration_s and t0 < duration_s / 2:
        return "10s to midpoint"
    return "second half"


def extract(tag_rows: list[dict], duration_s: float | None) -> Extracted:
    x = Extracted()
    product_ts: list[float] = []
    for t in tag_rows:
        d, k, v = t["dimension"], t["kind"], t["value"]
        if d in TEXT_DIMS:
            setattr(x, d, v)
            continue
        if d == "scenes" and k == "ordered":
            if t.get("ordinal") == 0:
                x.facets.add(("opening_scene", v))
            raw = t.get("raw_value") or ""
            if ("product" in raw or "packaging" in raw) and t.get("t_start_s") is not None:
                product_ts.append(float(t["t_start_s"]))
            continue
        if d in SKIP_DIMS:
            continue
        if k in ("single", "multi", "open"):
            x.facets.add((d, v))
        elif k == "numeric":
            try:
                x.numeric[d] = float(v)
            except (TypeError, ValueError):
                pass
    x.first_product_s = min(product_ts) if product_ts else None
    x.facets.add(("first_product_shown", reveal_bin(x.first_product_s, duration_s)))
    return x


def tercile_labels(
    values: dict[str, float],
) -> tuple[dict[str, str], tuple[float, float] | None]:
    vals = sorted(values.values())
    if len(vals) < 6:
        return {}, None
    lo, hi = vals[len(vals) // 3], vals[(2 * len(vals)) // 3]
    labels = {k: ("low" if v < lo else "high" if v >= hi else "mid") for k, v in values.items()}
    return labels, (float(lo), float(hi))
