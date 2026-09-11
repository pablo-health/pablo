# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which adjustment group a reason code may legitimately arrive under.

``PR-253`` bills a federal sequestration cut to the client; ``CO-1`` writes
off a deductible. Either is a payer bug or a parse bug.

Warns, never holds: the arithmetic in :mod:`app.claims.remittance_lines`
decides whether a client can be billed, and an unfamiliar code combination
is novelty rather than error.

The normative CAQH CORE Rule 360 table is not vendored. What is here is the
narrower set of pairings that contradict the code's own published
description in :mod:`app.claims.codes.carc`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...models.claims_responses import Adjustment

#: Deductible, coinsurance, copay, blood deductible — the client's by
#: definition, so any group but ``PR`` contradicts the code.
PATIENT_ONLY_REASONS: frozenset[str] = frozenset({"1", "2", "3", "66"})

#: Sequestration: comes out of the payment, never off the client.
NEVER_PATIENT_REASONS: frozenset[str] = frozenset({"253"})

_PATIENT_RESPONSIBILITY = "PR"


def mispaired(adjustments: Iterable[Adjustment]) -> list[tuple[str, str]]:
    """Every ``(group, reason)`` pair that contradicts the reason's own meaning.

    Returned rather than logged so the caller decides what it is worth.
    """
    found: list[tuple[str, str]] = []
    for adjustment in adjustments:
        group = adjustment.group_code.upper()
        reason = adjustment.reason_code
        patient = group == _PATIENT_RESPONSIBILITY
        if (reason in PATIENT_ONLY_REASONS and not patient) or (
            reason in NEVER_PATIENT_REASONS and patient
        ):
            found.append((group, reason))
    return found
