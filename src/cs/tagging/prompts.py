from __future__ import annotations

import hashlib
import json

from cs.tagging.taxonomy import Taxonomy

SYSTEM = (
    "You are a senior performance-creative analyst. You decompose paid social ad creatives into "
    "structured elements so a team can learn which elements drive performance. Look at every "
    "image carefully; for videos you are given the actual video file -- watch it and listen to "
    "its audio track (voiceover, dialogue, music, sound effects) directly, rather than relying on "
    "a transcript; for carousels the images are the cards in swipe order, one image per card. For "
    "each dimension listed, pick exactly one value from its allowed values (or "
    "write a short free-text value where no values are listed), give a confidence between 0 and "
    "1, and cite concrete visual or textual evidence -- for videos, evidence may cite what is "
    "heard as well as what is seen. Emit one entry per dimension; do not skip dimensions."
)


def user_text(taxonomy: Taxonomy, fmt: str, copy: dict) -> str:
    lines = [f"Creative format: {fmt}", "", "Ad copy fields:"]
    for k in ("headline", "primary_text", "description", "cta_type", "link_url"):
        if copy.get(k):
            lines.append(f"- {k}: {copy[k]}")
    lines += ["", "Dimensions to tag:"]
    for d in taxonomy.for_format(fmt):
        vals = ", ".join(d.values) if d.values else "free text, one sentence"
        lines.append(f"- {d.name} ({d.group}): {d.description} Allowed values: {vals}")
    lines += ["", 'Return JSON: {"tags": [{"dimension", "value", "confidence", "evidence"}, ...]}']
    return "\n".join(lines)


def prompt_sha(system: str, user: str, schema: dict) -> str:
    h = hashlib.sha256()
    h.update(system.encode())
    h.update(b"\x00")
    h.update(user.encode())
    h.update(b"\x00")
    h.update(json.dumps(schema, sort_keys=True).encode())
    return h.hexdigest()
