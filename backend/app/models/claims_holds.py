# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A remittance the engine refused to bill a client from.

An 835 states the client's share twice — as a claim total (``CLP05``) and
itemised across the lines — and every line and claim must account for the
gap between charged and paid. When two of those disagree, one is wrong, and
neither is a number to put on somebody's bill.

A hold is a row with a state and timestamps rather than a log line, because
somebody has to find it, act on it, and have the acting recorded.

No amount here is safe to bill from. ``patient_responsibility_cents`` is
what the PAYER says, kept so a practice that takes the payer at its word
bills that figure rather than one re-derived later.

No clinical content. ``patient_id`` is here so the row is isolated by the
same ``has_patient_access`` policy as its claim.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

#: Which self-statement failed. One hold names one reason — the practice's
#: decision is the same whichever it was.
#:
#: * ``patient_responsibility`` — ``CLP05`` vs the ``PR`` adjustments. The
#:   strongest: an independent cross-check on the half that bills a client
#:   (X12 RFI #2548).
#: * ``line_balance`` — ``SVC02`` vs ``SVC03`` plus the line's adjustments.
#: * ``claim_balance`` — ``CLP03`` vs ``CLP04`` plus every adjustment.
#:
#: The last two are TR3 005010X221A1 §1.10.2. No ``transaction_balance``:
#: that needs ``PLB``, which nothing here parses.
HoldReason = Literal["patient_responsibility", "line_balance", "claim_balance"]

HOLD_REASONS: tuple[str, ...] = ("patient_responsibility", "line_balance", "claim_balance")

#: ``acknowledged`` means somebody has seen it, not that the number is
#: settled. There is no ``expired``: nothing releases a hold but a person.
HoldState = Literal["open", "acknowledged", "resolved"]

HOLD_STATES: tuple[str, ...] = ("open", "acknowledged", "resolved")

#: How a hold ended. ``bill_as_stated`` writes the withheld row; ``waived``
#: writes none. ``parse_error`` and ``payer_inconsistent`` say WHY the
#: numbers disagreed and say nothing about what was billed.
HoldFinding = Literal["bill_as_stated", "waived", "parse_error", "payer_inconsistent"]

HOLD_FINDINGS: tuple[str, ...] = (
    "bill_as_stated",
    "waived",
    "parse_error",
    "payer_inconsistent",
)

#: The findings that write the withheld ledger row.
FINDINGS_THAT_BILL: frozenset[str] = frozenset({"bill_as_stated"})


class RemittanceHold(BaseModel):
    """One remittance whose own numbers disagree with each other.

    ``posting_key`` is what the posting path already dedupes on, so a
    redelivered remittance writes one hold rather than one per delivery.
    """

    id: str
    claim_id: str
    #: Copied from the claim so ``has_patient_access`` isolates the hold
    #: without a join. Never different from the claim's own.
    patient_id: str
    #: ``CLP01`` — how a person finds the remittance in the vendor's portal.
    control_number: str
    posting_key: str
    state: HoldState = "open"
    reason: HoldReason

    #: What the payer said: ``CLP05``, ``SVC02`` or ``CLP03`` by reason.
    stated_cents: int
    #: The same figure from the rest of the document.
    computed_cents: int
    #: What the payer says the client owes in total — NOT "what we withheld".
    #: The withheld row is the difference against the ledger, which moves;
    #: see :func:`app.claims.holds.withheld_cents`.
    patient_responsibility_cents: int

    #: Set on ``line_balance`` only.
    line_control_number: str | None = None

    #: Every ``group_code``/``reason_code`` pair, in the order sent. Codes
    #: only — the first thing anybody triaging the disagreement looks up.
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
        """How far apart the two statements are. Derived, not stored."""
        return self.stated_cents - self.computed_cents

    @property
    def is_open(self) -> bool:
        """Still withholding a ledger row. ``acknowledged`` counts as open."""
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
