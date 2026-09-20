# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Instrument licence repository — what a practice has permission to use.

One table, one row per act. Recording permission writes a row; withdrawing
it stamps ``revoked_at`` on the row that was current. Nothing here belongs
to a patient or to a clinician, so there is no ``has_patient_access`` call
and no ``user_id`` predicate: the session is already pointed at one
practice's schema, and every query below is scoped by being on it.

Two reads, and the difference is what each screen asks.
:meth:`InstrumentLicenseRepository.list_active` is the set the form builder
and the publisher consult — permission that is in force right now.
:meth:`InstrumentLicenseRepository.active_for_code` is the same question
about one instrument, which is what the settings screen shows beside it.
Withdrawn rows stay readable through neither; they are there so the record
of who said what survives, and the audit log is where that record is read.

Rows are plain ``dict[str, object]`` matching the column layout, the same
shape the other repositories in this package hand back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class InstrumentLicenseRepository(ABC):
    """Abstract base class for instrument licence attestation data access."""

    @abstractmethod
    def add(self, row: dict[str, object]) -> dict[str, object]:
        """Insert one attestation."""

    @abstractmethod
    def list_active(self) -> list[dict[str, object]]:
        """Every attestation in force, oldest first."""

    @abstractmethod
    def active_for_code(self, instrument_code: str) -> dict[str, object] | None:
        """The attestation in force for one instrument, or ``None``."""

    @abstractmethod
    def revoke(self, attestation_id: str, revoked_at: datetime) -> dict[str, object] | None:
        """Stamp one attestation withdrawn. ``None`` if there is no such id.

        Idempotence is the service's business, not this one's: it reads the
        row in force before calling, so a second withdrawal has nothing to
        pass here.
        """


class InMemoryInstrumentLicenseRepository(InstrumentLicenseRepository):
    """In-memory implementation for tests and local runs."""

    def __init__(self) -> None:
        self._rows: list[dict[str, object]] = []

    def add(self, row: dict[str, object]) -> dict[str, object]:
        stored = dict(row)
        self._rows.append(stored)
        return dict(stored)

    def list_active(self) -> list[dict[str, object]]:
        return [dict(row) for row in self._rows if row.get("revoked_at") is None]

    def active_for_code(self, instrument_code: str) -> dict[str, object] | None:
        for row in self._rows:
            if row["instrument_code"] == instrument_code and row.get("revoked_at") is None:
                return dict(row)
        return None

    def revoke(self, attestation_id: str, revoked_at: datetime) -> dict[str, object] | None:
        for row in self._rows:
            if row["id"] == attestation_id:
                row["revoked_at"] = revoked_at
                return dict(row)
        return None
