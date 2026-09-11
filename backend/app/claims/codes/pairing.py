# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which adjustment group a reason code may legitimately arrive under.

A CAS adjustment is a pair: the group says who absorbs the money, the reason
says why. Not every pair is meaningful. ``PR-253`` would be a sequestration
cut — a reduction in what the federal government pays a provider — billed to
the client; ``CO-1`` would be a deductible the practice absorbed. Both are
either a payer bug or a parse bug, and either way somebody should look.

**This is a warning, never a hold.** A pairing nobody has seen before is not
evidence that the arithmetic is wrong, and the arithmetic is what decides
whether a client can be billed (:mod:`app.claims.remittance_lines`). Making
an unfamiliar code combination stop a practice's billing would be a check
that fires on novelty rather than on error.

**What is and is not encoded here, because the difference matters.** The
normative source for code combinations is the CAQH CORE Rule 360 table,
which is ACA-mandated and published as a downloadable spreadsheet. It is
NOT vendored here — importing it is its own piece of work, with its own
provenance and refresh story. What this module encodes instead is the much
smaller set of pairings that contradict the code's own published
description in :mod:`app.claims.codes.carc`: a code whose text says
"Deductible Amount" is the client's share by definition, whichever table one
reads. That is a real check with a narrow reach, rather than a
half-remembered copy of a table we do not hold.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ...models.claims_responses import Adjustment

#: Codes whose published description names them as the client's own share.
#: ``1`` "Deductible Amount", ``2`` "Coinsurance Amount", ``3``
#: "Co-payment Amount", ``66`` "Blood Deductible." — each is definitionally
#: money the client owes, so any group but ``PR`` contradicts the code.
PATIENT_ONLY_REASONS: frozenset[str] = frozenset({"1", "2", "3", "66"})

#: Codes whose published description names them as a reduction the provider
#: or payer absorbs, so ``PR`` contradicts the code. ``253``
#: "Sequestration - reduction in federal payment" is the worked example the
#: 835 literature always reaches for: it comes out of the payment, and a
#: payer that billed it to a client would be billing them for a federal
#: budget measure.
NEVER_PATIENT_REASONS: frozenset[str] = frozenset({"253"})

_PATIENT_RESPONSIBILITY = "PR"


def mispaired(adjustments: Iterable[Adjustment]) -> list[tuple[str, str]]:
    """Every ``(group, reason)`` pair that contradicts the reason's own meaning.

    Empty for the ordinary remittance. Returned rather than logged so the
    caller decides what a suspicious pairing is worth — today, a warning
    beside the arithmetic that actually gates a bill.
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
