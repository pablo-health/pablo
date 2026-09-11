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

from .codes.pairing import mispaired

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from ..models.claims import ClaimLine
    from ..models.claims_holds import HoldReason
    from ..models.claims_responses import Adjustment, RemittanceClaim

logger = logging.getLogger(__name__)

#: What the client owes: deductible, copay, coinsurance. The one group code
#: that moves money onto a client's ledger, and the only one this module
#: needs to recognise — every other group is recorded and totalled nowhere.
PATIENT_RESPONSIBILITY = "PR"

#: ``CLP02`` for a claim the payer refused. The payer's own word for it, not
#: a reading of the adjustment codes.
DENIED = "4"

#: ``CLP02`` for a payer taking back an earlier adjudication. Its amounts are
#: the negation of what was posted before, and the standard exempts it from
#: the patient-responsibility cross-check below.
REVERSAL = "22"


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


@dataclass(frozen=True, slots=True)
class Disagreement:
    """One way this remittance's own numbers fail to account for each other.

    ``stated`` is what the payer asserted directly; ``computed`` is the same
    figure worked out from the rest of the document. They are kept apart,
    rather than reduced to a delta, because which side is which is the first
    thing anybody reading the disagreement needs.
    """

    reason: HoldReason
    stated_cents: int
    computed_cents: int
    #: The service line at fault, on ``line_balance`` only.
    line_control_number: str | None = None

    @property
    def delta_cents(self) -> int:
        return self.stated_cents - self.computed_cents


def _signed_total(adjustments: Iterable[Adjustment]) -> int:
    """Every adjustment, all group codes, summed as the payer signed them.

    The balancing identities count all six CAS triplets of every group,
    which is the whole point of them: an adjustment we could not classify
    still has to be accounted for somewhere, and one we dropped is exactly
    the gap these checks exist to notice.
    """
    return sum(a.amount_cents for a in adjustments)


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
    ones do — a payer may report the client's share at either level, and the
    standard forbids reporting the same adjustment at both, so adding them is
    not double counting.

    A reversal is exempt. Its amounts negate an earlier adjudication and the
    standard does not require the stated total to match the itemisation
    there, so checking it would report a disagreement on a claim that is
    behaving correctly.
    """
    return _patient_responsibility(remittance) is None


def _patient_responsibility(remittance: RemittanceClaim) -> Disagreement | None:
    """``CLP05`` against the ``PR`` itemisation. X12 RFI #2548."""
    if remittance.claim_status_code == REVERSAL:
        return None
    itemised = _total(remittance.adjustments, PATIENT_RESPONSIBILITY) + sum(
        _total(line.adjustments, PATIENT_RESPONSIBILITY) for line in remittance.lines
    )
    if itemised == remittance.patient_responsibility_cents:
        return None
    return Disagreement(
        reason="patient_responsibility",
        stated_cents=remittance.patient_responsibility_cents,
        computed_cents=itemised,
    )


def _line_balance(remittance: RemittanceClaim) -> Disagreement | None:
    """Each line's ``SVC02`` against ``SVC03`` plus every adjustment on it.

    TR3 005010X221A1 section 1.10.2, the service-line balancing identity.
    The first line that fails is the one reported: the practice's decision
    is the same whichever line it was, and a document that has already told
    us not to trust it does not become more trustworthy by enumerating how
    many ways.
    """
    for line in remittance.lines:
        computed = line.paid_cents + _signed_total(line.adjustments)
        if computed != line.charge_cents:
            return Disagreement(
                reason="line_balance",
                stated_cents=line.charge_cents,
                computed_cents=computed,
                line_control_number=line.line_control_number,
            )
    return None


def _claim_balance(remittance: RemittanceClaim) -> Disagreement | None:
    """``CLP03`` against ``CLP04`` plus every adjustment on the claim.

    TR3 005010X221A1 section 1.10.2, the claim balancing identity. Claim-
    level and line-level adjustments are added together: the standard
    forbids reporting the same adjustment at both levels, so the sum is the
    claim's whole explanation of the gap between charged and paid.
    """
    adjustments = _signed_total(remittance.adjustments) + sum(
        _signed_total(line.adjustments) for line in remittance.lines
    )
    computed = remittance.paid_cents + adjustments
    if computed == remittance.total_charge_cents:
        return None
    return Disagreement(
        reason="claim_balance",
        stated_cents=remittance.total_charge_cents,
        computed_cents=computed,
    )


#: The hard checks, in the order a disagreement is reported in. Patient
#: responsibility comes first deliberately: it is the one that speaks
#: directly about the number that bills a client, so when a remittance
#: fails more than one check that is the one worth naming.
#:
#: Not here, and not faked: the transaction identity
#: ``BPR02 = sum(CLP04) - sum(PLB)``. Nothing in this engine parses ``PLB``
#: provider-level adjustments — no model, no field, no parser branch — so a
#: sum computed without them would be wrong on exactly the remittances the
#: check exists to catch, and would report a disagreement on a payer that
#: had done nothing wrong. A check that fires falsely on the normal case
#: gets switched off, which is worse than not having written it.
_CHECKS: tuple[Callable[[RemittanceClaim], Disagreement | None], ...] = (
    _patient_responsibility,
    _line_balance,
    _claim_balance,
)


def _warn_about_pairings(remittance: RemittanceClaim) -> None:
    """Say so when an adjustment's group contradicts its own reason code.

    Soft on purpose: see :mod:`app.claims.codes.pairing`. A ``PR-253`` is
    either a payer bug or a parse bug and is worth a person's attention,
    but an unfamiliar pairing is not a reason to stop billing.
    """
    suspicious = mispaired(
        [*remittance.adjustments, *(a for line in remittance.lines for a in line.adjustments)]
    )
    for group, reason in suspicious:
        logger.warning(
            "remittance_suspicious_code_pairing control_number=%s group=%s reason=%s",
            remittance.patient_control_number,
            group,
            reason,
        )


def disagreement_in(remittance: RemittanceClaim) -> Disagreement | None:
    """The first way this remittance fails to account for its own numbers.

    ``None`` means every identity the engine can check held, which is the
    ordinary case and the only one from which a client may be billed.

    An 835 is a balanced transaction: what was charged, what was paid and
    what was adjusted must account for each other at the line and at the
    claim, and the client's share must be stated and itemised to the same
    figure. Those are guaranteed by the standard rather than by any
    particular payer's care, which is what makes a failure worth acting on
    — it means our parse is wrong or the payer's file is, and either way
    the amounts on it are not ones to bill a real person from.

    Logged at WARNING on failure. A disagreement is an expected business
    event rather than an error: something has to be decided by a person,
    and nothing is broken.

    A suspicious CARC/group pairing is reported alongside and never
    returned. It is evidence that somebody should read the document, not
    evidence that a number is wrong, and the arithmetic is what decides
    whether a client can be billed.
    """
    _warn_about_pairings(remittance)
    for check in _CHECKS:
        found = check(remittance)
        if found is not None:
            logger.warning(
                "remittance_disagrees reason=%s control_number=%s "
                "line_control_number=%s stated=%d computed=%d",
                found.reason,
                remittance.patient_control_number,
                found.line_control_number,
                found.stated_cents,
                found.computed_cents,
            )
            return found
    return None


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
