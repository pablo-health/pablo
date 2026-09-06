# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Lookup API over the code tables, plus a coarse category per adjustment.

Everything here is a dictionary read. The tables are Python data so nothing
has to call out to learn what ``CO-45`` means, and mypy types every entry.
"""

from __future__ import annotations

from typing import Literal, get_args

from app.claims.codes.carc import CARC
from app.claims.codes.claim_status import CLAIM_STATUS
from app.claims.codes.groups import GROUPS
from app.claims.codes.rarc import RARC

Category = Literal[
    "paid",
    "patient_responsibility",
    "contractual",
    "enrollment",
    "coding",
    "timely_filing",
    "eligibility",
    "needs_records",
    "duplicate",
    "other",
]

CATEGORIES: tuple[Category, ...] = get_args(Category)

# Hand-written over the adjustments a behavioral-health practice actually sees.
# The category names the kind of follow-up, not the payer's wording: a
# "coding" denial gets a corrected claim, "needs_records" gets an
# authorization or documentation, "enrollment" gets a credentialing call.
# A pair that is not listed is "other"; a line with no adjustment is "paid".
_CATEGORY_BY_ADJUSTMENT: dict[tuple[str, str], Category] = {
    # Deductible, coinsurance, copay, and the plan's own non-covered decisions.
    ("PR", "1"): "patient_responsibility",
    ("PR", "2"): "patient_responsibility",
    ("PR", "3"): "patient_responsibility",
    ("PR", "96"): "patient_responsibility",
    ("PR", "119"): "patient_responsibility",
    ("PR", "204"): "patient_responsibility",
    # Fee-schedule and bundling write-offs the contract already agreed to.
    ("CO", "45"): "contractual",
    ("CO", "59"): "contractual",
    ("CO", "253"): "contractual",
    # The claim said something the payer's edits rejected: fix and resubmit.
    ("CO", "4"): "coding",
    ("CO", "5"): "coding",
    ("CO", "6"): "coding",
    ("CO", "7"): "coding",
    ("CO", "9"): "coding",
    ("CO", "11"): "coding",
    ("CO", "12"): "coding",
    ("CO", "16"): "coding",
    ("CO", "97"): "coding",
    ("CO", "146"): "coding",
    ("CO", "181"): "coding",
    ("CO", "182"): "coding",
    ("CO", "236"): "coding",
    ("CO", "29"): "timely_filing",
    # Coverage was not in force, or the payer cannot find the member.
    ("CO", "26"): "eligibility",
    ("CO", "27"): "eligibility",
    ("CO", "31"): "eligibility",
    ("CO", "32"): "eligibility",
    ("CO", "33"): "eligibility",
    ("CO", "140"): "eligibility",
    ("CO", "177"): "eligibility",
    ("CO", "200"): "eligibility",
    ("PR", "26"): "eligibility",
    ("PR", "27"): "eligibility",
    ("PR", "31"): "eligibility",
    ("PR", "200"): "eligibility",
    # Authorization or documentation the payer wants before it will pay.
    ("CO", "50"): "needs_records",
    ("CO", "197"): "needs_records",
    ("CO", "198"): "needs_records",
    ("CO", "226"): "needs_records",
    ("CO", "252"): "needs_records",
    ("CO", "18"): "duplicate",
    ("OA", "18"): "duplicate",
    # The rendering or billing provider is not set up with this payer.
    ("CO", "8"): "enrollment",
    ("CO", "B7"): "enrollment",
    ("CO", "183"): "enrollment",
    ("CO", "185"): "enrollment",
    ("CO", "208"): "enrollment",
}


def describe_carc(code: str) -> str | None:
    """Published description of a claim adjustment reason code, or None if unknown."""
    return CARC.get(code.strip().upper())


def describe_rarc(code: str) -> str | None:
    """Published description of a remittance advice remark code, or None if unknown."""
    return RARC.get(code.strip().upper())


def describe_group(code: str) -> str:
    """Published description of a claim adjustment group code.

    An unknown group is described as such rather than dropped: the remittance
    still carries a group, and a person reading it needs to see something.
    """
    key = code.strip().upper()
    return GROUPS.get(key) or f"Unknown adjustment group {key}"


def describe_claim_status(code: str) -> str:
    """Description of an 835 CLP02 claim status code, or an "unknown" placeholder."""
    key = code.strip()
    return CLAIM_STATUS.get(key) or f"Unknown claim status {key}"


def categorize(group: str | None, carc: str | None) -> Category:
    """Coarse follow-up category for one adjustment.

    A line with no adjustment at all (no group and no CARC) is ``paid``. A
    pair the table does not name is ``other``.
    """
    group_key = (group or "").strip().upper()
    carc_key = (carc or "").strip().upper()
    if not group_key and not carc_key:
        return "paid"
    return _CATEGORY_BY_ADJUSTMENT.get((group_key, carc_key), "other")
