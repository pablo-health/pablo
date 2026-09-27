# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain model for a patient's request to have a medication refilled."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

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
    never shown to the patient.
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


@dataclass(frozen=True)
class RefillQueueEntry:
    """A pending request as the practice's queue shows it: the request and whose it is."""

    request: RefillRequest
    patient_first_name: str
    patient_last_name: str
    patient_preferred_name: str | None
