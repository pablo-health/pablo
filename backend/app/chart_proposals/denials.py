# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An allergy denial: a statement that there are no allergies, and nothing else.

Read where a note's allergies field is seeded (``recorded``) and where a
proposed allergy entry is checked (``families``): a denial is no known drug
allergies on the chart, never an allergy whose substance is "none".
"""

from __future__ import annotations

import re

#: The item a seeded allergy denial is proposed under, and what it reads.
NKDA = "NKDA"
NKDA_TEXT = "No known drug allergies (NKDA)"

#: The words that make a statement a denial.
_NEGATIONS = frozenset(
    ["no", "nope", "none", "nothing", "never", "nkda", "nka", "not", "denies", "denied"]
)

#: Every word an allergy denial is made of. A statement with any other word in it
#: ("No, but penicillin gives me a rash") names something, and is not a denial.
_DENIAL_WORDS = _NEGATIONS | frozenset(
    [
        "any",
        "anything",
        "known",
        "i",
        "im",
        "ive",
        "am",
        "do",
        "dont",
        "that",
        "know",
        "of",
        "to",
        "my",
        "knowledge",
        "had",
        "have",
        "a",
        "reaction",
        "reactions",
        "allergic",
        "allergy",
        "allergies",
        "drug",
        "drugs",
        "medication",
        "medications",
        "medicine",
        "medicines",
        "reported",
        "report",
        "per",
        "client",
        "clients",
        "patient",
        "patients",
        "the",
        "and",
        "or",
        "other",
    ]
)


def is_allergy_denial(text: str) -> bool:
    """Whether a statement about allergies says there are none, and nothing else."""
    words = set(re.findall(r"[a-z]+", re.sub(r"['\u2019]", "", text.lower())))
    return bool(words & _NEGATIONS) and words <= _DENIAL_WORDS
