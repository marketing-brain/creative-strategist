from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from cs.tagging.vocab import normalize

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")
KINDS = ("numeric", "single", "multi", "ordered", "open", "text")
EVIDENCE_MAX = 500
ROW_KEYS = (
    "dimension",
    "kind",
    "value",
    "raw_value",
    "confidence",
    "evidence",
    "t_start_s",
    "ordinal",
)


@dataclass(frozen=True)
class Dimension:
    name: str
    group: str
    applies_to: tuple[str, ...]
    values: tuple[str, ...] | None
    description: str
    kind: str = "single"
    timestamped: bool = False
    max_values: int | None = None
    vocab_hint: int = 0
    # Formats for which code, not the model, produces the value (spec §1 layer 0).
    computed_for: tuple[str, ...] = ()

    @property
    def computed(self) -> bool:
        return self.kind == "numeric" or bool(self.computed_for)

    def computed_in(self, fmt: str) -> bool:
        return self.kind == "numeric" or fmt in self.computed_for


@dataclass(frozen=True)
class Taxonomy:
    version: str
    sha256: str
    dimensions: tuple[Dimension, ...]
    nested_formats: tuple[str, ...] = ()

    def for_format(self, fmt: str) -> list[Dimension]:
        return [d for d in self.dimensions if fmt in d.applies_to]

    def is_nested(self, fmt: str) -> bool:
        return fmt in self.nested_formats

    def open_dimensions(self, fmt: str) -> list[Dimension]:
        return [d for d in self.for_format(fmt) if d.kind == "open"]

    def model_dimensions(self, fmt: str) -> list[Dimension]:
        """Dimensions the model is asked for: everything not computed in code for `fmt`."""
        return [d for d in self.for_format(fmt) if not d.computed_in(fmt)]

    # ---- schemas -----------------------------------------------------------------------

    def json_schema(self, fmt: str) -> dict:
        if self.is_nested(fmt):
            return self._nested_schema(fmt)
        return self._flat_schema(fmt)

    def _flat_schema(self, fmt: str) -> dict:
        dims = self.for_format(fmt)
        return {
            "type": "object",
            "properties": {
                "tags": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "dimension": {"type": "string", "enum": [d.name for d in dims]},
                            "value": {"type": "string"},
                            "confidence": {"type": "number"},
                            "evidence": {"type": "string"},
                        },
                        "required": ["dimension", "value", "confidence", "evidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["tags"],
            "additionalProperties": False,
        }

    def _nested_schema(self, fmt: str) -> dict:
        def obj(props: dict) -> dict:
            return {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }

        def enum(d: Dimension) -> dict:
            return {"type": "string", "enum": list(d.values or ())}

        def items(props: dict) -> dict:
            return {"type": "array", "items": obj(props)}

        single, multi, ordered, open_, text = {}, {}, {}, {}, {}
        for d in self.model_dimensions(fmt):
            if d.kind == "single":
                single[d.name] = obj(
                    {
                        "value": enum(d),
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    }
                )
            elif d.kind == "multi":
                props = {"value": enum(d), "evidence": {"type": "string"}}
                if d.timestamped:
                    props["t_start_s"] = {"type": "number"}
                multi[d.name] = items(props)
            elif d.kind == "ordered":
                ordered[d.name] = items({"value": enum(d), "t_start_s": {"type": "number"}})
            elif d.kind == "open":
                open_[d.name] = items({"slug": {"type": "string"}, "evidence": {"type": "string"}})
            elif d.kind == "text":
                text[d.name] = {"type": "string"}
        return obj(
            {
                "single": obj(single),
                "multi": obj(multi),
                "ordered": obj(ordered),
                "open": obj(open_),
                "text": obj(text),
            }
        )

    # ---- validation --------------------------------------------------------------------

    def validate(self, payload, fmt: str) -> list[dict]:
        if self.is_nested(fmt):
            return self._validate_nested(payload, fmt)
        return self._validate_flat(payload, fmt)

    def _validate_flat(self, tags: list[dict], fmt: str) -> list[dict]:
        by_name = {d.name: d for d in self.for_format(fmt)}
        out: list[dict] = []
        for t in tags:
            d = by_name.get(t.get("dimension"))
            if d is None:
                continue
            value = str(t.get("value", "")).strip()
            if d.values is not None and value not in d.values:
                value = "other" if "other" in d.values else d.values[0]
            conf = float(t.get("confidence", 0.0))
            out.append(
                {
                    "dimension": d.name,
                    "value": value,
                    "confidence": max(0.0, min(1.0, conf)),
                    "evidence": str(t.get("evidence", ""))[:EVIDENCE_MAX],
                }
            )
        best: dict[str, dict] = {}
        for tag in out:
            existing = best.get(tag["dimension"])
            if existing is None or tag["confidence"] > existing["confidence"]:
                best[tag["dimension"]] = tag
        return [best[name] for name in dict.fromkeys(t["dimension"] for t in out)]

    def _validate_nested(self, payload: dict, fmt: str) -> list[dict]:
        """Flatten the model's nested profile to tag rows. Raises ValueError on a rule break
        (spec §3): a missing single, a value outside its enum, empty or out-of-order beats."""
        if not isinstance(payload, dict):
            raise ValueError("profile payload is not an object")
        rows: list[dict] = []
        for d in self.model_dimensions(fmt):
            section = payload.get(_SECTION[d.kind])
            if section is None:
                section = {}
            if not isinstance(section, dict):
                raise ValueError(f"{d.kind} section is not an object")
            if d.kind == "single":
                rows.append(self._single_row(d, section.get(d.name)))
            elif d.kind == "multi":
                rows.extend(self._multi_rows(d, section.get(d.name)))
            elif d.kind == "ordered":
                rows.extend(self._ordered_rows(d, section.get(d.name)))
            elif d.kind == "open":
                rows.extend(self._open_rows(d, section.get(d.name)))
            elif d.kind == "text":
                rows.append(_row(d, str(section.get(d.name) or "")))
        return rows

    @staticmethod
    def _single_row(d: Dimension, item) -> dict:
        if not isinstance(item, dict):
            raise ValueError(f"{d.name}: missing")
        value = str(item.get("value", "")).strip()
        if value not in (d.values or ()):
            raise ValueError(f"{d.name}: value {value!r} not allowed")
        conf = max(0.0, min(1.0, float(item.get("confidence", 0.0))))
        return _row(d, value, confidence=conf, evidence=item.get("evidence"))

    @staticmethod
    def _multi_rows(d: Dimension, items) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for item in _items(d, items):
            value = str(_item(d, item).get("value", "")).strip()
            if value not in (d.values or ()):
                raise ValueError(f"{d.name}: value {value!r} not allowed")
            if value in seen:
                continue
            seen.add(value)
            t = _seconds(item.get("t_start_s")) if d.timestamped else None
            out.append(_row(d, value, evidence=item.get("evidence"), t_start_s=t))
        return out

    @staticmethod
    def _ordered_rows(d: Dimension, items) -> list[dict]:
        items = _items(d, items)
        if not items:
            raise ValueError(f"{d.name}: must not be empty")
        out: list[dict] = []
        last = float("-inf")
        for i, item in enumerate(items):
            value = str(_item(d, item).get("value", "")).strip()
            if value not in (d.values or ()):
                raise ValueError(f"{d.name}: value {value!r} not allowed")
            t = _seconds(item.get("t_start_s"))
            if t is None or t < last:
                raise ValueError(f"{d.name}: t_start_s must be present and non-decreasing")
            last = t
            out.append(_row(d, value, t_start_s=t, ordinal=i))
        return out

    @staticmethod
    def _open_rows(d: Dimension, items) -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for item in _items(d, items):
            raw = str(_item(d, item).get("slug", ""))
            slug = normalize(raw)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            out.append(_row(d, slug, raw_value=raw, evidence=item.get("evidence")))
            if d.max_values is not None and len(out) >= d.max_values:
                break
        return out


_SECTION = {
    "single": "single",
    "multi": "multi",
    "ordered": "ordered",
    "open": "open",
    "text": "text",
}


def _items(d: Dimension, items) -> list:
    """The collection a model returned for `d`, as a list. A missing collection is empty;
    anything that is not a list is a rule break, not a crash."""
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(f"{d.name}: not a list")
    return items


def _item(d: Dimension, item) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"{d.name}: item is not an object")
    return item


def _seconds(v) -> float | None:
    if v is None:
        return None
    return max(0.0, float(v))


def _row(
    d: Dimension,
    value: str,
    *,
    raw_value: str | None = None,
    confidence: float = 1.0,
    evidence: str | None = None,
    t_start_s: float | None = None,
    ordinal: int | None = None,
) -> dict:
    return {
        "dimension": d.name,
        "kind": d.kind,
        "value": value,
        "raw_value": raw_value,
        "confidence": confidence,
        "evidence": str(evidence or "")[:EVIDENCE_MAX],
        "t_start_s": t_start_s,
        "ordinal": ordinal,
    }


def computed_rows(
    taxonomy: Taxonomy, fmt: str, values: dict[str, str | float | None]
) -> list[dict]:
    """Rows for the format's computed dimensions from `values` (name -> value); a None or
    missing value produces no row (spec §5: a failed cut probe omits cuts_per_10s)."""
    out: list[dict] = []
    for d in taxonomy.for_format(fmt):
        if not d.computed_in(fmt):
            continue
        v = values.get(d.name)
        if v is None:
            continue
        out.append(_row(d, str(v), evidence="computed"))
    return out


@lru_cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    p = path or DEFAULT_PATH
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    dims = []
    for d in data["dimensions"]:
        values = tuple(str(v) for v in d["values"]) if d.get("values") else None
        kind = d.get("kind") or ("single" if values else "text")
        if kind not in KINDS:
            raise ValueError(f"{d['name']}: unknown kind {kind!r}")
        dims.append(
            Dimension(
                name=d["name"],
                group=d["group"],
                applies_to=tuple(d["applies_to"]),
                values=values,
                description=d["description"],
                kind=kind,
                timestamped=bool(d.get("timestamped", False)),
                max_values=d.get("max_values"),
                vocab_hint=int(d.get("vocab_hint", 0)),
                computed_for=tuple(d.get("computed_for", ())),
            )
        )
    return Taxonomy(
        version=str(data["version"]),
        sha256=hashlib.sha256(raw).hexdigest(),
        dimensions=tuple(dims),
        nested_formats=tuple(data.get("nested_formats", ())),
    )
