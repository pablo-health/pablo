# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer decided about each service on a claim.

A claim total says $300 charged, $180 paid. It cannot say whether both
sessions were allowed with a deductible applied, or one paid and one denied
— a balance conversation and an appeal conversation. The 835's service-line
loops carry that, and this reads it onto the claim's lines.

Each adjustment carries an X12 group code, and the group decides who is out
the money:

* ``CO`` contractual: the network discount. Nobody owes it.
* ``PR`` patient responsibility: deductible, copay, coinsurance.
* ``OA`` / ``PI`` other and payer-initiated: recorded, counted in neither
  total, not safe to guess at.

Only ``PR`` bills a client, and that is definitional rather than empirical:
a provider may bill a client only for ``PR``.

``allowed_cents`` is whatever the payer reported (``AMT*B6``) and nothing
otherwise. "Charge less contractual" is wrong out of network (the write-off
is ``PR45``, no ``CO``), under sequestration (``CO253`` comes out of the
payment), on secondary claims (``OA23`` carries the primary's numbers), and
for payers that price with ``PI``. Each returns a plausible wrong number, so
an unreported allowed amount stays unreported.

Caveat: the test payer pays in full and adjusts nothing, so every adjusting
remittance below is constructed. The first real 835 confirms the rest.
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

    Kept as two figures rather than a delta: which side is which is the
    first thing a reader needs.
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

    All six CAS triplets of every group: an adjustment we could not classify
    still has to be accounted for, and one we dropped is the gap these
    checks exist to notice.
    """
    return sum(a.amount_cents for a in adjustments)


def patient_responsibility_agrees(remittance: RemittanceClaim) -> bool:
    """Does the payer's own total for the client match what we read line by line?

    ``CLP05`` and the ``PR`` itemisation are two independent statements of
    one number, so an adjustment read into the wrong group shows up as a
    disagreement rather than as a wrong bill.

    Claim-level adjustments count the same as line-level ones: the standard
    forbids reporting one adjustment at both levels, so adding them is not
    double counting.

    Reversals are exempt — their amounts negate an earlier adjudication and
    the standard does not require the totals to match there.
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

    TR3 005010X221A1 §1.10.2. The first failing line is the one reported —
    the practice's decision is the same whichever it was.
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

    TR3 005010X221A1 §1.10.2. Claim-level and line-level adjustments add
    together — one adjustment may not appear at both levels.
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


#: In report order. Patient responsibility first: it speaks directly about
#: the number that bills a client.
#:
#: Not here, and deliberately not faked: ``BPR02 = sum(CLP04) - sum(PLB)``.
#: Nothing parses ``PLB``, so that sum would be wrong on exactly the
#: remittances the check exists to catch.
_CHECKS: tuple[Callable[[RemittanceClaim], Disagreement | None], ...] = (
    _patient_responsibility,
    _line_balance,
    _claim_balance,
)


def _warn_about_pairings(remittance: RemittanceClaim) -> None:
    """Say so when an adjustment's group contradicts its own reason code.

    Soft on purpose — see :mod:`app.claims.codes.pairing`.
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

    ``None`` means every identity held — the ordinary case, and the only one
    a client may be billed from. A failure means our parse is wrong or the
    payer's file is; either way the amounts are not ones to bill from.

    WARNING, not ERROR: a disagreement is an expected business event that
    needs a person, not a broken system.

    A suspicious CARC/group pairing is reported alongside and never
    returned — it is a reason to read the document, not a wrong number.
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
