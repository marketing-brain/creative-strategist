from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).with_name("taxonomy.yaml")


@dataclass(frozen=True)
class Dimension:
    name: str
    group: str
    applies_to: tuple[str, ...]
    values: tuple[str, ...] | None
    description: str


@dataclass(frozen=True)
class Taxonomy:
    version: str
    sha256: str
    dimensions: tuple[Dimension, ...]

    def for_format(self, fmt: str) -> list[Dimension]:
        return [d for d in self.dimensions if fmt in d.applies_to]

    def json_schema(self, fmt: str) -> dict:
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

    def validate(self, tags: list[dict], fmt: str) -> list[dict]:
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
            out.append({
                "dimension": d.name,
                "value": value,
                "confidence": max(0.0, min(1.0, conf)),
                "evidence": str(t.get("evidence", ""))[:500],
            })
        return out


@lru_cache
def load_taxonomy(path: Path | None = None) -> Taxonomy:
    p = path or DEFAULT_PATH
    raw = p.read_bytes()
    data = yaml.safe_load(raw)
    dims = tuple(
        Dimension(
            name=d["name"], group=d["group"], applies_to=tuple(d["applies_to"]),
            values=tuple(str(v) for v in d["values"]) if d.get("values") else None,
            description=d["description"],
        )
        for d in data["dimensions"]
    )
    return Taxonomy(version=str(data["version"]), sha256=hashlib.sha256(raw).hexdigest(), dimensions=dims)
