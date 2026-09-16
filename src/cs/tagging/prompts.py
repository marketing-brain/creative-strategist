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


SYSTEM_V2 = (
    "You are a senior performance-creative analyst. You decompose one paid-social video ad "
    "into a structured profile that will be used to cluster ads into creative-concept "
    "families, so precision and consistency matter more than nuance. You are given the "
    "actual video file: watch every frame and listen to the full audio track. Rules: "
    "(1) Every t_start is a clock time measured from the start of the video, written as "
    'minutes:seconds, e.g. "0:03", "0:47", "1:13". Tenths are allowed ("0:03.5"). '
    "(2) hook_devices are judged on the first 5 seconds only. "
    "(3) messenger = expert_on_camera and proof_devices = expert_credential require a "
    "credential stated on screen or in speech (pharmacist, doctor, RD, PhD); years of "
    "experience or a professional's recommendation without a credential do not count. "
    "(4) creator_off_camera means hands, point-of-view or body without a face. "
    "(5) For product_reveal, a generic pill, gummy or capsule shown in hand counts as the "
    "product. "
    "(6) beats are the ad's sections in order of appearance with non-decreasing t_start_s; "
    "every ad has at least hook; add a beat only where the content changes role. "
    "(7) Open slugs are lowercase snake_case of 1-4 words. concept_tags name the creative "
    "idea the way a strategist would and never contain a brand name. When the prompt lists "
    "existing slugs for a field, reuse one that fits and coin a new one only when none does. "
    "(8) transcript is verbatim speech with no timestamps; an empty string when there is no "
    "speech. hook_verbatim is the exact first on-screen text, then the first spoken "
    "sentence, separated by ' / '. "
    "(9) scenes is a complete shot list from 0:00 to the end: a new scene whenever the shot "
    "type or what is on screen changes; its face/product/packaging booleans describe that "
    "scene only. "
    "(10) overlay_texts is every on-screen text verbatim, in order, with the time it first "
    "appears; do not include subtitles that transcribe the speech. "
    "(11) proof_devices counts only what is visibly shown or explicitly spoken. "
    "(12) Every list may be empty except beats, scenes and concept_tags. Emit every field "
    "of the schema; cite concrete visual or audio evidence for each value."
)


def user_text_nested(
    taxonomy: Taxonomy,
    fmt: str,
    copy: dict,
    hints: dict[str, list[tuple[str, int]]],
    duration_s: float | None = None,
) -> str:
    lines = [f"Creative format: {fmt}"]
    if duration_s is not None:
        clock = f"{int(duration_s // 60)}:{duration_s % 60:04.1f}"
        lines.append(
            f"Video length: {duration_s:.1f} seconds ({clock}). Every t_start is a clock time "
            f"between 0:00 and {clock}."
        )
    lines += ["", "Ad copy fields:"]
    for k in ("headline", "primary_text", "description", "cta_type", "link_url"):
        if copy.get(k):
            lines.append(f"- {k}: {copy[k]}")
    lines += ["", "Profile fields:"]
    for d in taxonomy.model_dimensions(fmt):
        vals = ", ".join(d.values) if d.values else ""
        if d.kind == "single":
            lines.append(f"- {d.name} (single, {d.group}): {d.description} Allowed values: {vals}")
        elif d.kind == "multi":
            ts = " Each item carries t_start as minutes:seconds." if d.timestamped else ""
            lines.append(
                f"- {d.name} (multi, {d.group}): {d.description} Allowed values: {vals}{ts}"
            )
        elif d.kind == "ordered":
            flags = (
                " Each item also carries booleans: " + ", ".join(d.flags) + "." if d.flags else ""
            )
            lines.append(
                f"- {d.name} (ordered, {d.group}): {d.description} Allowed values: {vals} "
                f"Each item carries t_start as minutes:seconds.{flags}"
            )
        elif d.kind == "open":
            cap = f" At most {d.max_values}." if d.max_values else ""
            lines.append(f"- {d.name} (open, {d.group}): {d.description}{cap}")
            existing = hints.get(d.name) or []
            if existing:
                lines.append(
                    "  Existing slugs (count): "
                    + ", ".join(f"{slug} ({n})" for slug, n in existing)
                )
        elif d.kind == "text":
            lines.append(f"- {d.name} (text): {d.description}")
        elif d.kind == "series":
            lines.append(
                f"- {d.name} (series, {d.group}): {d.description} Each item carries t_start as "
                "minutes:seconds and text."
            )
    lines += ["", "Return JSON matching the schema exactly."]
    return "\n".join(lines)


def prompt_sha(system: str, user: str, schema: dict) -> str:
    h = hashlib.sha256()
    h.update(system.encode())
    h.update(b"\x00")
    h.update(user.encode())
    h.update(b"\x00")
    h.update(json.dumps(schema, sort_keys=True).encode())
    return h.hexdigest()
