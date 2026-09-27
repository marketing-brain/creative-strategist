"""Words an ad for a supplement must not say, checked in code on every line that will be spoken
or shown. Supplements may make structure/function claims but not disease claims (FDA, 21 CFR
101.93: nothing that diagnoses, treats, cures or prevents a disease); Meta's personal-attributes
rule forbids "you" + a condition ("Struggling with insomnia?"). A hit blocks the line; a person
decides what to say instead. Deliberately strict: "treat yourself" is caught too."""

from __future__ import annotations

import re

DISEASE_VERB = re.compile(r"\b(cures?|cured|treats?|treated|treatment|prevents?|prevented|heals?|healed|diagnos\w*|remed(y|ies))\b", re.I)
DISEASE = re.compile(
    r"\b(insomnia|depression|anxiety disorder|diabetes|cancer|hypertension|adhd|alzheimer'?s|arthritis|disease|disorder)\b",
    re.I,
)
YOU_CONDITION = re.compile(
    r"\b(are you|do you|you're|you are|if you have|struggling with)\b[^.?!]{0,30}\b(depress\w*|anxious|anxiety|insomnia|"
    r"overweight|obese|stressed|diabet\w*|can't sleep|cannot sleep)\b",
    re.I,
)


def problems(text: str) -> list[str]:
    out = []
    if m := DISEASE_VERB.search(text):
        out.append(f"“{m.group(0)}” reads as a disease claim (supplements may not claim to cure, treat or prevent)")
    if m := DISEASE.search(text):
        out.append(f"names a disease or disorder (“{m.group(0)}”)")
    if m := YOU_CONDITION.search(text):
        out.append(f"asks about the viewer's condition (“{m.group(0)}”), which Meta's ad rules forbid")
    return out
