# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer decided about each service on a claim.

A claim-level remittance says a claim charged at $300 was paid $180. It
cannot say whether the payer allowed both sessions and applied a deductible,
or paid one in full and denied the other. Those are different conversations —
one with the client about a balance, one with the payer about an appeal — and
a practice cannot tell them apart from the claim total.

The 835's service-line loops carry that detail, and this reads it onto the
claim's own lines.

**Where the numbers come from.** Each line reports what was charged and what
was paid, plus adjustments explaining the gap. Every adjustment carries a
group code — the X12 standard's, not the vendor's — and the group is what
decides who is out the money:

* ``CO`` contractual obligation: the discount the practice agreed to by
  joining the network. Nobody owes it; it is written off.
* ``PR`` patient responsibility: deductible, copay, coinsurance. The client
  owes it.
* ``OA`` / ``PI`` other and payer-initiated: neither of the above, and not
  safe to guess at, so they are recorded and left out of both totals.

``allowed_cents`` — what the payer agreed the service was worth — is the
charge less the contractual write-off. It is not reported directly and has to
be derived, which is why the derivation lives here in one place with the
reasoning attached rather than inline at a call site.

**A caveat worth keeping.** The clearinghouse's test payer pays every claim
in full and adjusts nothing, so no captured remittance exercises the
adjustment paths. The group-code meanings are the published X12 standard
rather than anything read off this vendor, and the arithmetic below is tested
against constructed remittances. The first real payer's 835 is the thing that
confirms it; until then this is careful reading of a specification, not
evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models.claims import ClaimLine
    from ..models.claims_responses import Adjustment, RemittanceClaim

logger = logging.getLogger(__name__)

#: The discount the practice agreed to. Written off, owed by nobody.
CONTRACTUAL = "CO"

#: What the client owes: deductible, copay, coinsurance.
PATIENT_RESPONSIBILITY = "PR"


@dataclass(frozen=True, slots=True)
class LinePosting:
    """What one service line's adjudication says to write."""

    #: What the payer agreed the service was worth: the charge less the
    #: contractual write-off. ``None`` when the line reported no charge to
    #: derive it from.
    allowed_cents: int | None
    paid_cents: int
    patient_responsibility_cents: int
    #: Every adjustment on the line, in the order the payer sent them —
    #: including the ones neither total counts. A practice appealing a line
    #: needs the reason codes, and dropping the ones we could not classify
    #: would lose exactly the unusual case somebody is looking into.
    adjustments: list[dict[str, object]]


def _total(adjustments: Iterable[Adjustment], group: str) -> int:
    return sum(a.amount_cents for a in adjustments if a.group_code.upper() == group)


def posting_for_line(
    charge_cents: int, paid_cents: int, adjustments: Sequence[Adjustment]
) -> LinePosting:
    """One line's numbers, from what it charged, was paid, and was adjusted."""
    contractual = _total(adjustments, CONTRACTUAL)
    return LinePosting(
        allowed_cents=charge_cents - contractual if charge_cents else None,
        paid_cents=paid_cents,
        patient_responsibility_cents=_total(adjustments, PATIENT_RESPONSIBILITY),
        adjustments=[
            {
                "group_code": a.group_code,
                "reason_code": a.reason_code,
                "amount_cents": a.amount_cents,
            }
            for a in adjustments
        ],
    )


def postings_for(remittance: RemittanceClaim) -> dict[str, LinePosting]:
    """Each adjudicated line's numbers, keyed by its own control number.

    The control number is the practice's own, echoed back per line, which is
    what ties a payer's service line to the session it was billed for.
    """
    return {
        line.line_control_number: posting_for_line(
            line.charge_cents, line.paid_cents, line.adjustments
        )
        for line in remittance.lines
        if line.line_control_number
    }


def applied_to(lines: Sequence[ClaimLine], remittance: RemittanceClaim) -> list[ClaimLine]:
    """``lines`` with each adjudicated one carrying what the payer decided.

    A line the remittance does not mention is returned untouched rather than
    zeroed: the payer said nothing about it, which is not the same as saying
    it was worth nothing. A line the remittance mentions and the claim does
    not is logged and dropped — it cannot be posted anywhere, and inventing
    a line to hold it would put a service on the claim that was never billed.
    """
    postings = postings_for(remittance)
    known = {line.line_control_number for line in lines}
    for control_number in postings.keys() - known:
        logger.warning(
            "remittance_line_unmatched control_number=%s claim_control_number=%s",
            control_number,
            remittance.patient_control_number,
        )
    return [
        line
        if (posting := postings.get(line.line_control_number)) is None
        else line.model_copy(
            update={
                "allowed_cents": posting.allowed_cents,
                "paid_cents": posting.paid_cents,
                "patient_resp_cents": posting.patient_responsibility_cents,
                "adjustments": posting.adjustments,
            }
        )
        for line in lines
    ]
