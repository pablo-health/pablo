# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deadline defaults a new payer row is created with.

The common floor lives on the table (90 / 90 / 180 days). Medicare's timely
filing window is a year, and that exception is decided here rather than in
the DDL so a schema default stays one number and the rule that overrides it
is readable, testable code.

A payer is taken to be Medicare when its electronic payer id says so — the
id starts with ``MEDICARE`` — or when it is one of the CMS payer ids in
``MEDICARE_PAYER_IDS``. That set is the seam a payer-directory lookup fills
in; typed-from-the-card payers only ever hit the prefix rule.
"""

from __future__ import annotations

from ..db.models import DEFAULT_TIMELY_FILING_DAYS

#: Timely filing window for a Medicare payer, in days.
MEDICARE_TIMELY_FILING_DAYS = 365

#: Electronic payer ids the payer directory marks as Medicare, beyond the
#: ``MEDICARE`` prefix. Empty until a directory lookup populates it; kept as
#: a named constant so that change is one line here rather than a new rule.
MEDICARE_PAYER_IDS: frozenset[str] = frozenset()

_MEDICARE_PREFIX = "MEDICARE"


def normalize_payer_id(payer_id: str) -> str:
    """The electronic payer id as it is compared: trimmed and upper-cased."""
    return payer_id.strip().upper()


def is_medicare_payer_id(payer_id: str) -> bool:
    normalized = normalize_payer_id(payer_id)
    return normalized.startswith(_MEDICARE_PREFIX) or normalized in MEDICARE_PAYER_IDS


def default_timely_filing_days(payer_id: str) -> int:
    """How long after the service a claim may be filed, absent a stated value."""
    if is_medicare_payer_id(payer_id):
        return MEDICARE_TIMELY_FILING_DAYS
    return DEFAULT_TIMELY_FILING_DAYS
