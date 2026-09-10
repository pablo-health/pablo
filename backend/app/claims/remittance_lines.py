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

``allowed_cents`` — what the payer agreed the service was worth — is whatever
the payer reported (``AMT*B6``) and nothing otherwise. It is deliberately not
derived. The obvious formula, charge less the contractual write-off, is wrong
in several ordinary cases: out of network the write-off arrives as ``PR45``
with no ``CO`` at all, Medicare's sequestration (``CO253``) comes out of the
payment rather than the allowed amount, a secondary payer's ``OA23`` carries
the primary's numbers, and some payers price with ``PI`` where others use
``CO``. Each of those returns a number that looks reasonable and is wrong, so
an unreported allowed amount stays unreported.

Only ``PR`` drives what a client is billed. That one is safe on definitional
rather than empirical grounds — the group codes are normative, and a provider
may bill a client only for adjustments carrying ``PR`` — which is why it is
the number the ledger is written from and ``allowed`` is left informational.

**A caveat worth keeping.** The clearinghouse's test payer pays every claim
in full and adjusts nothing, so no captured remittance exercises the
adjustment paths, and the arithmetic below is tested against constructed
remittances. The first real payer's 835 is what confirms the rest.
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

#: What the client owes: deductible, copay, coinsurance. The one group code
#: that moves money onto a client's ledger, and the only one this module
#: needs to recognise — every other group is recorded and totalled nowhere.
PATIENT_RESPONSIBILITY = "PR"

#: ``CLP02`` for a claim the payer refused. The payer's own word for it, not
#: a reading of the adjustment codes.
DENIED = "4"


@dataclass(frozen=True, slots=True)
class LinePosting:
    """What one service line's adjudication says to write."""

    #: What the payer said the service was worth, when it said. ``None``
    #: means the payer did not report it — never a derived stand-in.
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
    paid_cents: int,
    adjustments: Sequence[Adjustment],
    *,
    allowed_cents: int | None = None,
    denied: bool = False,
) -> LinePosting:
    """One line's numbers, from what the payer paid, adjusted and allowed.

    ``denied`` is the payer's own claim status, not a reading of the
    adjustment codes, and it settles what an absent allowed amount means.
    The standard has payers omit the field rather than send a zero, so
    "absent" covers two opposite situations: a payer that did not itemise,
    and a payer that allowed nothing. On a refused claim it is the second,
    and saying nothing there would report a blank where the answer is known.
    """
    return LinePosting(
        allowed_cents=0 if allowed_cents is None and denied else allowed_cents,
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
    denied = remittance.claim_status_code == DENIED
    return {
        line.line_control_number: posting_for_line(
            line.paid_cents,
            line.adjustments,
            allowed_cents=line.allowed_cents,
            denied=denied,
        )
        for line in remittance.lines
        if line.line_control_number
    }


def patient_responsibility_agrees(remittance: RemittanceClaim) -> bool:
    """Does the payer's own total for the client match what we read line by line?

    The strongest check available on the half of this that bills somebody,
    and the only one that needs no payer to have shown us anything first.

    A payer states the claim's patient-responsibility total once (``CLP05``)
    and then itemises it across the service lines. Those are two independent
    statements of the same number, so reading an adjustment into the wrong
    group shows up here as a disagreement rather than as a client being
    quietly billed the wrong amount. It fires on the very first real
    remittance rather than waiting for anybody to reason about it.

    Claim-level adjustments count towards the total the same way line-level
    ones do — a payer may report the client's share at either level.
    """
    itemised = _total(remittance.adjustments, PATIENT_RESPONSIBILITY) + sum(
        _total(line.adjustments, PATIENT_RESPONSIBILITY) for line in remittance.lines
    )
    if itemised == remittance.patient_responsibility_cents:
        return True
    logger.warning(
        "remittance_patient_responsibility_disagrees control_number=%s stated=%d itemised=%d",
        remittance.patient_control_number,
        remittance.patient_responsibility_cents,
        itemised,
    )
    return False


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
