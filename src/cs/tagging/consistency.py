"""Agreement between two v2 profiles of the same creative (spec §9). Text is not scored."""

from __future__ import annotations

from cs.tagging.taxonomy import Taxonomy


def _values(rows: list[dict], dimension: str) -> list[dict]:
    return [r for r in rows if r["dimension"] == dimension]


def agreement(
    rows_a: list[dict], rows_b: list[dict], taxonomy: Taxonomy, fmt: str
) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for d in taxonomy.for_format(fmt):
        a, b = _values(rows_a, d.name), _values(rows_b, d.name)
        if d.kind in ("single", "numeric"):
            va = a[0]["value"] if a else None
            vb = b[0]["value"] if b else None
            out[d.name] = 1.0 if va == vb else 0.0
        elif d.kind in ("multi", "open"):
            sa, sb = {r["value"] for r in a}, {r["value"] for r in b}
            out[d.name] = 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)
        elif d.kind == "ordered":
            seq_a = [r["value"] for r in sorted(a, key=lambda r: r["ordinal"] or 0)]
            seq_b = [r["value"] for r in sorted(b, key=lambda r: r["ordinal"] or 0)]
            out[d.name] = 1.0 if seq_a == seq_b else 0.0
        else:
            out[d.name] = None
    return out


def summarize(per_creative: list[dict[str, float | None]]) -> dict[str, float | None]:
    names: list[str] = list(dict.fromkeys(k for p in per_creative for k in p))
    out: dict[str, float | None] = {}
    for name in names:
        scores = [p[name] for p in per_creative if p.get(name) is not None]
        out[name] = round(sum(scores) / len(scores), 3) if scores else None
    return out
