# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Claim Adjustment Group Codes, as published by X12.

Source: X12 External Code Lists, "Claim Adjustment Group Codes",
https://x12.org/codes/claim-adjustment-group-codes
List version: 5/20/2018 (the "updated" date shown on the page).
Retrieved: 2026-09-06.

The group code is the first half of a CAS adjustment (``CO-45``, ``PR-1``):
it says who absorbs the adjustment, and the CARC says why. ``CR`` was
retired when the 835 moved to version 5010, but older remittances still carry
it, so it is described here alongside the four current codes.
"""

from __future__ import annotations

GROUPS: dict[str, str] = {
    "CO": "Contractual Obligation",
    "CR": "Corrections and Reversal",
    "OA": "Other Adjustment",
    "PI": "Payor Initiated Reduction",
    "PR": "Patient Responsibility",
}
