# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remittance and denial code tables as data, with a lookup API.

Payers speak in codes: CARC adjustment reasons, RARC remarks, adjustment
group codes, and 835 claim status codes. The tables live in this package as
Python dictionaries generated from the public X12 code lists (each module
cites its list version and retrieval date), so posting a remittance or
showing a denial never has to call out to learn what ``CO-45`` means.
"""

from app.claims.codes.lookup import (
    CATEGORIES,
    Category,
    categorize,
    describe_carc,
    describe_claim_status,
    describe_group,
    describe_rarc,
)

__all__ = [
    "CATEGORIES",
    "Category",
    "categorize",
    "describe_carc",
    "describe_claim_status",
    "describe_group",
    "describe_rarc",
]
