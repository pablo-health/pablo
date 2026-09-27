# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refill request repository — two arms, one per principal.

The **patient arm** takes the ``patient_id`` of the resolved principal and
matches on it; there is no grant to check, because a patient reaches only
their own rows (and the row policy says the same thing underneath).

The **clinician arm** takes the clinician's ``user_id`` and gates on
``has_patient_access`` — the ``patient_clinicians`` grant that scopes notes,
messages and medications. Reads a clinician may not make come back ``None``
or empty, the same shape as "no such row"; a decision they may not make
raises :class:`RefillRequestAccessDeniedError`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from typing import TYPE_CHECKING, Literal

from ..models.refill_request import REFILL_STATUS_REQUESTED, RefillQueueEntry, RefillRequest

if TYPE_CHECKING:
    from datetime import datetime

# ``pending`` is the queue: everything still waiting, oldest first, because
# the request that has waited longest is the one to answer next. ``recent``
# is what was answered lately, newest answer first, bounded — it is there so
# a prescriber can see what they just did, not to page through history.
RefillQueueView = Literal["pending", "recent"]
RECENT_DECISIONS_LIMIT = 20


class RefillRequestAccessDeniedError(Exception):
    """A clinician tried to decide a request on a patient they have no grant for."""

    def __init__(self, request_id: str, user_id: str) -> None:
        super().__init__(f"user {user_id!r} has no access to refill request {request_id!r}")
        self.request_id = request_id
        self.user_id = user_id


class RefillRequestAlreadyDecidedError(Exception):
    """The request was answered already; a request has exactly one decision."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"refill request {request_id!r} was already decided")
        self.request_id = request_id


class RefillRequestRepository(ABC):
    # --- patient arm ---

    @abstractmethod
    def list_medication_options(self, patient_id: str) -> list[tuple[str, str, str]]:
        """``(id, drug_name, dose)`` for the patient's active, undeleted medications."""

    @abstractmethod
    def get_medication_option(
        self, medication_id: str, patient_id: str
    ) -> tuple[str, str, str] | None:
        """One of :meth:`list_medication_options`, or ``None`` if it is not one of them."""

    @abstractmethod
    def add(self, request: RefillRequest) -> RefillRequest:
        """Store a new request."""

    @abstractmethod
    def list_for_patient(self, patient_id: str) -> list[RefillRequest]:
        """The patient's own requests, newest first."""

    # --- clinician arm ---

    @abstractmethod
    def list_queue(self, user_id: str, view: RefillQueueView) -> list[RefillQueueEntry]:
        """Requests on every patient the clinician has a grant on. See :data:`RefillQueueView`."""

    @abstractmethod
    def get_entry(self, request_id: str, user_id: str) -> RefillQueueEntry | None:
        """One request with its patient's name, or ``None`` if absent or inaccessible."""

    @abstractmethod
    def decide(
        self,
        request_id: str,
        user_id: str,
        *,
        status: str,
        prescriber_note: str | None,
        decided_at: datetime,
    ) -> RefillRequest:
        """Record the decision.

        Raises :class:`RefillRequestAccessDeniedError` when the request is
        absent or the clinician has no grant, and
        :class:`RefillRequestAlreadyDecidedError` when it has left
        ``requested`` — including when another clinician answered it a
        moment earlier.
        """


class InMemoryRefillRequestRepository(RefillRequestRepository):
    """In-memory repository for unit tests.

    Seed patients with :meth:`add_patient`, medications with
    :meth:`add_medication`, and clinician grants with :meth:`grant_access`.
    """

    def __init__(self) -> None:
        self._requests: dict[str, RefillRequest] = {}
        self._medications: dict[str, tuple[str, str, str, bool]] = {}
        self._patients: dict[str, tuple[str, str, str | None]] = {}
        self._access: set[tuple[str, str]] = set()

    # --- test setup helpers ---

    def add_patient(
        self, patient_id: str, first_name: str, last_name: str, preferred_name: str | None = None
    ) -> None:
        self._patients[patient_id] = (first_name, last_name, preferred_name)

    def add_medication(
        self, medication_id: str, patient_id: str, drug_name: str, dose: str, *, active: bool = True
    ) -> None:
        self._medications[medication_id] = (patient_id, drug_name, dose, active)

    def grant_access(self, patient_id: str, user_id: str) -> None:
        self._access.add((patient_id, user_id))

    def _entry(self, request: RefillRequest) -> RefillQueueEntry:
        first, last, preferred = self._patients.get(request.patient_id, ("", "", None))
        return RefillQueueEntry(
            request=replace(request),
            patient_first_name=first,
            patient_last_name=last,
            patient_preferred_name=preferred,
        )

    # --- patient arm ---

    def list_medication_options(self, patient_id: str) -> list[tuple[str, str, str]]:
        return [
            (med_id, drug, dose)
            for med_id, (owner, drug, dose, active) in self._medications.items()
            if owner == patient_id and active
        ]

    def get_medication_option(
        self, medication_id: str, patient_id: str
    ) -> tuple[str, str, str] | None:
        return next(
            (m for m in self.list_medication_options(patient_id) if m[0] == medication_id), None
        )

    def add(self, request: RefillRequest) -> RefillRequest:
        self._requests[request.id] = replace(request)
        return replace(request)

    def list_for_patient(self, patient_id: str) -> list[RefillRequest]:
        rows = [replace(r) for r in self._requests.values() if r.patient_id == patient_id]
        return sorted(rows, key=lambda r: r.created_at, reverse=True)

    # --- clinician arm ---

    def list_queue(self, user_id: str, view: RefillQueueView) -> list[RefillQueueEntry]:
        visible = [r for r in self._requests.values() if (r.patient_id, user_id) in self._access]
        if view == "pending":
            rows = sorted(
                (r for r in visible if r.status == REFILL_STATUS_REQUESTED),
                key=lambda r: r.created_at,
            )
        else:
            decided = [r for r in visible if r.decided_at is not None]
            rows = sorted(decided, key=lambda r: r.decided_at or r.created_at, reverse=True)[
                :RECENT_DECISIONS_LIMIT
            ]
        return [self._entry(r) for r in rows]

    def get_entry(self, request_id: str, user_id: str) -> RefillQueueEntry | None:
        request = self._requests.get(request_id)
        if request is None or (request.patient_id, user_id) not in self._access:
            return None
        return self._entry(request)

    def decide(
        self,
        request_id: str,
        user_id: str,
        *,
        status: str,
        prescriber_note: str | None,
        decided_at: datetime,
    ) -> RefillRequest:
        request = self._requests.get(request_id)
        if request is None or (request.patient_id, user_id) not in self._access:
            raise RefillRequestAccessDeniedError(request_id, user_id)
        if request.status != REFILL_STATUS_REQUESTED:
            raise RefillRequestAlreadyDecidedError(request_id)
        request.status = status
        request.prescriber_note = prescriber_note
        request.decided_by_user_id = user_id
        request.decided_at = decided_at
        request.updated_at = decided_at
        return replace(request)
