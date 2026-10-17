# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Plain-English names for the service codes a therapy practice bills most.

The superbill prints one beside each code, because the client reading it and
the insurer's claims reviewer both recognise "Therapy session — 60 minutes or
more" faster than ``90837``. The official CPT descriptors are the AMA's
copyrighted text, so these are our own words, written to help someone
recognise a code, not to reproduce the code set.

The same list is offered as suggestions in the browser
(``frontend/src/lib/serviceCodes.ts``); ``test_service_codes.py`` fails when
the two disagree. A code not listed here prints as the code alone.
"""

from __future__ import annotations

COMMON_SERVICE_CODES: dict[str, str] = {
    "90791": "First visit — diagnostic evaluation",
    "90792": "First visit — evaluation with medical services",
    "90832": "Therapy session — around 30 minutes",
    "90834": "Therapy session — around 45 minutes",
    "90837": "Therapy session — 60 minutes or more",
    # people-term-ok: these two codes are defined by whether the patient is present
    "90846": "Family session — patient not present",
    # people-term-ok: these two codes are defined by whether the patient is present
    "90847": "Family or couples session — patient present",
    "90853": "Group session",
    "90839": "Crisis session — first 60 minutes",
    "90840": "Crisis session — each further 30 minutes",
    "90845": "Psychoanalysis session",
    "90785": "Add-on — communication made the session more complex",
    "99202": "Medical visit, new — straightforward",
    "99203": "Medical visit, new — low complexity",
    "99204": "Medical visit, new — moderate complexity",
    "99205": "Medical visit, new — high complexity",
    "99212": "Medical visit, established — straightforward",
    "99213": "Medical visit, established — low complexity",
    "99214": "Medical visit, established — moderate complexity",
    "99215": "Medical visit, established — high complexity",
    "90833": "Therapy with a medical visit — around 30 minutes",
    "90836": "Therapy with a medical visit — around 45 minutes",
    "90838": "Therapy with a medical visit — 60 minutes or more",
}


def describe_service_code(code: str | None) -> str | None:
    """The plain-English name for ``code``, when it is one we know."""
    if not code:
        return None
    return COMMON_SERVICE_CODES.get(code.strip().upper())
