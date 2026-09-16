"""Open-vocabulary tags: slug normalization and the per-org canonical vocabulary."""

from __future__ import annotations

import re

MAX_SLUG = 48


def normalize(slug: str) -> str:
    """lowercase snake_case: non-alphanumerics become '_', runs collapse, ends are stripped,
    and the result is cut at 48 characters (then stripped again so it never ends in '_')."""
    s = re.sub(r"[^a-z0-9]+", "_", slug.lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:MAX_SLUG].rstrip("_")
