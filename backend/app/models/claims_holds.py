# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A remittance the engine refused to bill a client from.

An 835 is a balanced document that states its most important number twice.
The payer reports the client's share of a claim as a claim total
(``CLP05``) and again itemised across the service lines as ``PR``-group
adjustments; and every line and every claim must account for the whole gap
between what was charged and what was paid. Those are independent
statements, which is what makes them worth checking: when two of them
disagree at least one is wrong — either the payer's file or our reading of
it — and neither is a number to put on a real person's bill.

The engine's answer is to post what the payer paid, withhold the client's
ledger row, and write one of these. A hold is a row with a state and
timestamps rather than a log line or a boolean, because somebody has to be
able to find it, act on it, and have the acting be recorded.

**No amount here is safe to bill from.** ``stated_cents`` and
``computed_cents`` are recorded so a person can see the size and shape of
the disagreement; ``patient_responsibility_cents`` is what the payer says
the client owes, kept so a practice that decides to take the payer at its
word bills exactly that figure rather than one re-derived later from a
document nobody re-reads.

Nothing in this module carries clinical content. ``patient_id`` is here
because the row is isolated by the same ``has_patient_access`` policy as
the claim it belongs to; no name, date of service or diagnosis appears.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Which of the 835's self-statements failed. One hold names one reason —
#: the first check to fail, in the order :func:`app.claims.remittance_lines.
#: disagreements` runs them — because the practice's decision is the same
#: whichever it was, and a list of reasons invites triaging a document that
#: has already told us not to trust it.
#:
#: * ``patient_responsibility`` — ``CLP05`` against the ``PR``-group
#:   adjustments summed across the claim. The strongest of the three for
#:   this purpose: the payer states the total separately from the
#:   itemisation, so it is an independent cross-check on precisely the half
#:   of the split that bills a client. X12 RFI #2548.
#: * ``line_balance`` — one service line's ``SVC02`` against ``SVC03`` plus
#:   every adjustment on the line.
#: * ``claim_balance`` — the claim's ``CLP03`` against ``CLP04`` plus every
#:   adjustment on it, claim level and line level together.
#:
#: The last two are TR3 005010X221A1 §1.10.2 balancing identities (SNIP
#: level 3). There is no ``transaction_balance``: that identity is
#: ``BPR02 = sum(CLP04) - sum(PLB)``, and nothing in this engine parses
#: ``PLB`` provider-level adjustments, so the sum would be wrong in exactly
#: the cases the check exists for.
HoldReason = Literal["patient_responsibility", "line_balance", "claim_balance"]

HOLD_REASONS: tuple[str, ...] = ("patient_responsibility", "line_balance", "claim_balance")

#: Where a hold stands. ``open`` the moment it is raised; ``acknowledged``
#: once a person has said they have seen it, which silences the short
#: re-notification tier without claiming the number is settled;
#: ``resolved`` when somebody decided what to bill.
#:
#: There is no ``expired``. Time passing is not evidence that a
#: self-contradicting remittance was right, so nothing releases a hold
#: except a person deciding.
HoldState = Literal["open", "acknowledged", "resolved"]

HOLD_STATES: tuple[str, ...] = ("open", "acknowledged", "resolved")

#: How a hold ended, recorded on resolution and never guessed.
#:
#: * ``bill_as_stated`` — the practice accepted the payer's stated total and
#:   the withheld ledger row was written.
#: * ``waived`` — the practice chose not to bill the client at all.
#: * ``parse_error`` — the disagreement was ours; the 835 was fine.
#: * ``payer_inconsistent`` — the disagreement was the payer's.
#:
#: The last two are findings about *why*, reachable once somebody has looked
#: at the document. They say nothing about what was billed, which is why
#: they are a separate axis from the ledger row and not a substitute for
#: one of the first two.
HoldFinding = Literal["bill_as_stated", "waived", "parse_error", "payer_inconsistent"]

HOLD_FINDINGS: tuple[str, ...] = (
    "bill_as_stated",
    "waived",
    "parse_error",
    "payer_inconsistent",
)

#: Findings that mean the withheld ledger row was written after all.
#: ``waived`` and the two diagnostic findings write nothing.
FINDINGS_THAT_BILL: frozenset[str] = frozenset({"bill_as_stated"})


class RemittanceHold(BaseModel):
    """One remittance whose own numbers disagree with each other.

    ``posting_key`` is the key the posting path already dedupes on — the
    claim id and the vendor entry that carried the adjudication. Holding it
    here is what makes re-delivering the same remittance write one hold
    rather than one per delivery.
    """

    id: str
    claim_id: str
    #: Copied from the claim so the tenant schema's ``has_patient_access``
    #: policy isolates the hold without a join the policy engine would have
    #: to be taught. Never different from the claim's own.
    patient_id: str
    #: The practice's own control number for the claim (``CLP01``), which is
    #: how a person finds the remittance in the clearinghouse portal.
    control_number: str
    posting_key: str
    state: HoldState = "open"
    reason: HoldReason

    #: What the payer said, in the reason's own terms: ``CLP05`` for
    #: ``patient_responsibility``, ``SVC02`` for ``line_balance``,
    #: ``CLP03`` for ``claim_balance``.
    stated_cents: int
    #: The same figure worked out from the rest of the document: the ``PR``
    #: itemisation, or paid plus adjustments.
    computed_cents: int
    #: What the payer says the client owes for this claim in total — the
    #: figure the withheld ledger row would have been derived from.
    #:
    #: Deliberately NOT "the amount we withheld". A remittance states a
    #: balance rather than adding to one, so the row is the DIFFERENCE from
    #: what this claim has already billed, and that difference changes
    #: whenever anything else touches the ledger. Storing it would mean
    #: keeping a second copy of a number whose real home is the ledger.
    #: Readers subtract what is billed at the moment they ask; see
    #: :func:`app.claims.holds.withheld_cents`.
    patient_responsibility_cents: int

    #: The service line the disagreement is on, for ``line_balance``.
    #: ``None`` for the two claim-level reasons.
    line_control_number: str | None = None

    #: Every ``group_code``/``reason_code`` pair present on the claim, in
    #: the order the payer sent them. Codes only: a CARC is a number from a
    #: public list, and it is the first thing anybody looking at the
    #: disagreement wants.
    codes: list[dict[str, str]] = Field(default_factory=list)
    line_count: int = 0
    payer_name: str | None = None

    detected_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by_user_id: str | None = None
    finding: HoldFinding | None = None

    @property
    def delta_cents(self) -> int:
        """How far apart the two statements are, stated less computed.

        Derived rather than stored: it is the subtraction of two columns on
        the same row, and a stored copy is one more thing that can disagree
        with the numbers it describes.
        """
        return self.stated_cents - self.computed_cents

    @property
    def is_open(self) -> bool:
        """Is this hold still withholding a client's ledger row?

        ``acknowledged`` counts as open. Somebody saying they have seen the
        disagreement is not somebody deciding what to bill.
        """
        return self.state != "resolved"


# ---------------------------------------------------------------------------
# API shapes
# ---------------------------------------------------------------------------


class RemittanceHoldResponse(BaseModel):
    """One hold as the practice sees it.

    Carries the two figures that disagree and what the payer says the
    client owes, because a person deciding whether to bill it needs to see
    the size of the doubt. It does NOT carry the withheld ledger amount:
    that is the difference against a ledger that may have moved since, and
    is recomputed at the moment the practice chooses to bill.
    """

    id: str
    claim_id: str
    control_number: str
    state: HoldState
    reason: HoldReason
    stated_cents: int
    computed_cents: int
    delta_cents: int
    patient_responsibility_cents: int
    line_control_number: str | None = None
    codes: list[dict[str, str]] = Field(default_factory=list)
    line_count: int = 0
    payer_name: str | None = None
    detected_at: datetime
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    finding: HoldFinding | None = None


class RemittanceHoldListResponse(BaseModel):
    data: list[RemittanceHoldResponse]
    total: int


class ResolveHoldRequest(BaseModel):
    """How the practice decided.

    Both answers are always accepted while the hold is open. Nothing here
    takes a reason, a confirmation flag or an acknowledgement first: a
    practice acting on its own client's balance should not have to satisfy
    the software before the software will let it.
    """

    finding: HoldFinding
