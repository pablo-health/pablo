# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a covered client pays at the door.

One rule, in one place, because two callers need the same answer to the same
question and a clinician must never be shown one figure and charged another:
the queue row that offers "Charge copay" reads it to display the amount, and
the charge route reads it again to decide what to actually charge.

The order is deliberate. The practice's own override wins, because it is the
figure somebody read off the card or agreed in a contract; the stored
eligibility answer is next, because the payer said it, however long ago; and
when neither exists the answer is ``None`` — which the callers turn into
"ask the clinician", never into a guess and never into zero.

Nothing else on a 271 is used here. Coinsurance is a percentage of an
allowed amount nobody knows until the remittance arrives, and a deductible
is a running total the payer maintains; charging either at the visit means
inventing a number and collecting money against it. A 271 is not a payment
guarantee, and the one line on it a practice can act on at the door is the
copay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..claims.eligibility import summary_for_coverage

if TYPE_CHECKING:
    from ..models.coverage import PatientCoverage


def copay_cents(coverage: PatientCoverage | None) -> int | None:
    """The copay to take at the door: the override, else the payer's answer.

    ``None`` means nobody has said what it is — not that it is nothing. Zero
    is a real answer and a different one: a payer that priced this benefit at
    nothing leaves nothing to collect.
    """
    if coverage is None:
        return None
    if coverage.copay_override_cents is not None:
        return coverage.copay_override_cents
    summary = summary_for_coverage(coverage)
    return summary.copay_cents if summary is not None else None
