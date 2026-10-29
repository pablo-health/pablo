# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain model for a patient's request to have a medication refilled."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

REFILL_STATUS_REQUESTED = "requested"
REFILL_STATUS_APPROVED = "approved"
REFILL_STATUS_NEEDS_VISIT = "needs_visit"
REFILL_STATUS_DECLINED = "declined"

REFILL_STATUSES: frozenset[str] = frozenset(
    {
        REFILL_STATUS_REQUESTED,
        REFILL_STATUS_APPROVED,
        REFILL_STATUS_NEEDS_VISIT,
        REFILL_STATUS_DECLINED,
    }
)


@dataclass
class RefillRequest:
    """One refill request, from the patient who made it to the answer they got.

    ``medication_text`` is always set. When the patient picked a medication
    from their own list it is copied from that row at submit time, so the
    request keeps saying what was asked for even if the list changes later;
    ``medication_id`` then points at the row it came from. When the patient
    typed a name instead, ``medication_id`` is ``None``.

    ``prescriber_note`` is the practice's own note on the decision. It is
    never shown to the patient. Nor is ``due_at``, the time the practice
    means to have answered by (see :mod:`app.services.refill_turnaround`);
    ``None`` on requests from before the practice had a target.

    ``run_out_date`` is the client's own answer to how many days they have
    left, as a date; ``None`` when they left it blank.
    """

    id: str
    patient_id: str
    medication_id: str | None
    medication_text: str
    pharmacy_text: str | None
    patient_note: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    decided_by_user_id: str | None = None
    decided_at: datetime | None = None
    prescriber_note: str | None = None
    due_at: datetime | None = None
    run_out_date: date | None = None


#: Where a request's run-out date came from: the client's answer, or the
#: estimate from a prescription on file.
RunOutSource = Literal["client", "prescription"]


@dataclass(frozen=True)
class RefillRisk:
    """What the practice's read of a request knows about time and risk.

    Decided by :func:`app.models.refill_risk.assess`; see that module for
    where each fact comes from. Never on the patient surface.
    """

    run_out_date: date | None = None
    run_out_source: RunOutSource | None = None
    controlled: bool = False
    interruption_risk: bool = False


def queue_order_key(request: RefillRequest) -> tuple[bool, datetime, datetime]:
    """Soonest due first; requests with no due time after, oldest first."""
    return (request.due_at is None, request.due_at or request.created_at, request.created_at)


@dataclass(frozen=True)
class RefillQueueEntry:
    """A request as the practice's queue shows it: the request, whose it is, and its risk."""

    request: RefillRequest
    patient_first_name: str
    patient_last_name: str
    patient_preferred_name: str | None
    risk: RefillRisk = field(default_factory=RefillRisk)
