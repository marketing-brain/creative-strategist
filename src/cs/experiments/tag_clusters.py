"""Clusters read straight off the video tagger's own tags: who is on screen, whether it is
animated, and how long it is. No model call: the tagger already watched every frame, so these
are its facts, sorted by fixed rules the buyer can read.

A cluster is the combination of the three, e.g. "Expert testimonial · Live action · Late reveal".
Length was the third part first, but it mostly repeated who carries the ad (short = text only)
and split nothing cleanly inside a format; when the product first appears did (2026-09-25).
"""

from __future__ import annotations

import re
from typing import Any

# Who carries the ad, first matching rule wins (order matters: a podcast with a doctor is an
# interview; a doctor alone to camera is an expert testimonial).
WHO_ORDER = (
    "chat_screenshots", "interview", "reaction", "expert", "talking_head", "acted", "voiceover",
    "text_only", "other",
)
WHO_LABEL = {
    "chat_screenshots": "Chat screenshots",
    "interview": "Interview",
    "reaction": "Reacting to another video (3P)",
    "expert": "Expert testimonial",
    "talking_head": "Talking head",
    "acted": "Acted / skit",
    "voiceover": "Voiceover over footage",
    "text_only": "Text only",
    "other": "Other",
}
ANIM_LABEL = {"ai": "AI", "live": "Live action"}
DURATION = ((30, "short", "Short (<30s)"), (90, "medium", "Medium (30–90s)"), (None, "long", "Long (90s+)"))
REVEAL_S = 10.0  # early reveal: the product is on screen within the first 10 seconds
REVEAL_LABEL = {"early": "Early reveal (≤10s)", "late": "Late reveal (>10s)", "never": "Product never shown"}
# The tagger's own answer, used only when a video has no scene list.
_TAG_REVEAL = {"first_3s": "early", "3_to_10s": "early", "after_10s": "late", "never": "never"}


def reveal(first_product_s: float | None, has_scenes: bool, tags: dict[str, list[str]]) -> str:
    """From the scene list: when the first scene showing the product starts (measured more
    reliable than the tagger's direct answer, 0.92 vs 0.84 agreement)."""
    if has_scenes:
        if first_product_s is None:
            return "never"
        return "early" if first_product_s <= REVEAL_S else "late"
    return _TAG_REVEAL.get((tags.get("product_reveal") or [None])[0] or "", "never")


_INTERVIEW = re.compile(r"interview", re.I)
# "Podcast" only as a word in an ad set name (a format test like "New Format _ Podcast"): in ad
# names it is often a creator's handle or a reference ("…PodcastGaryBrekaStanfordRef").
_PODCAST_SET = re.compile(r"(?<![a-z])podcast(?![a-z])", re.I)


def who(tags: dict[str, list[str]], names: list[str] = (), adsets: list[str] = ()) -> str:
    """Who carries the ad, from the tagger's facts and the ad's own names, no model.

    Interview comes from the names ("Street interview", "Podcast"): the tags only record how
    many people are on screen, and two people on screen caught reaction videos, experts with
    someone in shot and voiceovers too (9 of 16 wrong, 2026-09-25). Reacting to someone else's
    video comes from the tagger's duet/stitch cue; reaction videos it did not mark as such fall
    back to their speaker."""
    one = lambda d: (tags.get(d) or [None])[0]  # noqa: E731
    messenger, narration = one("messenger"), one("narration")
    cues = set(tags.get("native_format_cues", []))
    if "message_screenshot" in cues:
        return "chat_screenshots"
    if any(n and _INTERVIEW.search(n) for n in names) or any(
        n and _PODCAST_SET.search(n) for n in adsets
    ):
        return "interview"
    if "duet_or_stitch" in cues:
        return "reaction"
    if messenger == "expert_on_camera":
        return "expert"
    if messenger in ("creator_on_camera", "founder_on_camera"):
        return "talking_head"
    if messenger == "actor_scripted":
        return "acted"
    if messenger == "creator_off_camera" or narration == "voiceover":
        return "voiceover"
    if messenger == "graphic_only" or narration == "none":
        return "text_only"
    return "other"


_AI = re.compile(r"(?<![a-z])(ai|animation|animated|claymation)(?![a-z])", re.I)


def animated(tags: dict[str, list[str]], names: list[str]) -> str:
    """AI-made in any form (animation, AI-generated people, AI songs) is one category. The
    tags alone miss AI-generated people, who read as real people, so the ad's own name and
    the names of the ad sets it ran in count too: every such series sits in an "AI …" ad set."""
    if "motion_graphics" in tags.get("production", []):
        return "ai"
    if any(n and _AI.search(n) for n in names):
        return "ai"
    return "live"


def duration(seconds: float | None) -> tuple[str, str]:
    if seconds is None:
        return "unknown", "Unknown length"
    for hi, key, label in DURATION:
        if hi is None or seconds < hi:
            return key, label
    return DURATION[-1][1], DURATION[-1][2]


def labels(
    tags: dict[str, list[str]],
    names: list[str],
    adsets: list[str],
    seconds: float | None,
    first_product_s: float | None = None,
    has_scenes: bool = False,
) -> dict[str, Any]:
    w, a, (d, dl) = who(tags, names, adsets), animated(tags, names), duration(seconds)
    r = reveal(first_product_s, has_scenes, tags)
    return {
        "who": (w, WHO_LABEL[w]),
        "animated": (a, ANIM_LABEL[a]),
        "reveal": (r, REVEAL_LABEL[r]),
        # Kept as a filter, no longer part of the cluster.
        "duration": (d, dl),
    }


# ---- the cluster itself: who carries it x reveal, all AI as one, and clusters under
# MIN_CLUSTER videos (counted over every labelled video in the experiment, whatever the page's
# filters) merged into OTHER. OTHER is a mixed bag, not a kind of ad: it gets no card and no
# read, and within-cluster comparisons leave it out.
MIN_CLUSTER = 5
OTHER = "Other"


def cluster_name(who_label: str, animated_label: str, reveal_label: str) -> str:
    return "AI" if animated_label == ANIM_LABEL["ai"] else f"{who_label} · {reveal_label}"


def cluster_map(labels: dict[Any, dict[str, str]]) -> dict[Any, str]:
    """key (footage id or sha256) -> cluster, from each key's stored display labels by axis."""
    raw = {
        k: cluster_name(a["who"], a["animated"], a["reveal"])
        for k, a in labels.items()
        if all(x in a for x in ("who", "animated", "reveal"))
    }
    sizes: dict[str, int] = {}
    for c in raw.values():
        sizes[c] = sizes.get(c, 0) + 1
    return {k: (c if sizes[c] >= MIN_CLUSTER else OTHER) for k, c in raw.items()}
