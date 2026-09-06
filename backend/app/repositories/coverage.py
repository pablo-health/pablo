# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Data access for coverage on file: the payer list and a client's plan.

Two repositories, one per table. Neither commits: both ride the request's
single transaction the same way the patient repository does, so a coverage
row and the audit entry describing it land together or not at all.

The in-memory implementations mirror the Postgres ones closely enough for
the route tests to exercise the same contracts — including the one-active-
coverage-per-client rule the database enforces with a partial unique index.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models.coverage import PatientCoverage, Payer


class ActiveCoverageExistsError(Exception):
    """A second active coverage was created for a client who already has one."""


class PayerRepository(ABC):
    """The practice's payer list."""

    @abstractmethod
    def list(self) -> list[Payer]:
        """Every payer, by name."""

    @abstractmethod
    def get(self, payer_row_id: str) -> Payer | None:
        """One payer by its row id, or ``None``."""

    @abstractmethod
    def find_typed(self, name: str, payer_id: str | None) -> Payer | None:
        """A payer already on the list matching what somebody typed.

        Matches on the name, case-insensitively, and on the electronic payer
        id when one was given. Used so a client typing the same card twice
        does not double the payer list.
        """

    @abstractmethod
    def create(self, payer: Payer) -> Payer:
        """Add a payer. Flushed, not committed."""

    @abstractmethod
    def update(self, payer: Payer) -> Payer:
        """Write the payer's current fields back. Flushed, not committed."""


class PatientCoverageRepository(ABC):
    """One client's coverage rows, the active one foremost."""

    @abstractmethod
    def get_active(self, patient_id: str) -> PatientCoverage | None:
        """The client's active primary coverage, or ``None``."""

    @abstractmethod
    def create(self, coverage: PatientCoverage) -> PatientCoverage:
        """Add a coverage row. Flushed, not committed.

        Raises :class:`ActiveCoverageExistsError` when the row is active and
        the client already has an active one — the database's partial unique
        index says the same thing, and the in-memory implementation mirrors
        it so callers meet the rule in tests too.
        """

    @abstractmethod
    def update(self, coverage: PatientCoverage) -> PatientCoverage:
        """Write the coverage's current fields back. Flushed, not committed."""


class InMemoryPayerRepository(PayerRepository):
    def __init__(self) -> None:
        self._payers: dict[str, Payer] = {}

    def list(self) -> list[Payer]:
        return sorted(self._payers.values(), key=lambda p: p.name.lower())

    def get(self, payer_row_id: str) -> Payer | None:
        return self._payers.get(payer_row_id)

    def find_typed(self, name: str, payer_id: str | None) -> Payer | None:
        wanted_name = name.strip().lower()
        wanted_id = payer_id.strip().upper() if payer_id else None
        for payer in self._payers.values():
            if payer.name.strip().lower() != wanted_name:
                continue
            if wanted_id is not None and payer.payer_id.strip().upper() != wanted_id:
                continue
            return payer
        return None

    def create(self, payer: Payer) -> Payer:
        self._payers[payer.id] = payer
        return payer

    def update(self, payer: Payer) -> Payer:
        payer.updated_at = utc_now()
        self._payers[payer.id] = payer
        return payer


class InMemoryPatientCoverageRepository(PatientCoverageRepository):
    def __init__(self) -> None:
        self._rows: dict[str, PatientCoverage] = {}

    def get_active(self, patient_id: str) -> PatientCoverage | None:
        for row in self._rows.values():
            if row.patient_id == patient_id and row.active:
                return row
        return None

    def create(self, coverage: PatientCoverage) -> PatientCoverage:
        if coverage.active and self.get_active(coverage.patient_id) is not None:
            raise ActiveCoverageExistsError(coverage.patient_id)
        self._rows[coverage.id] = coverage
        return coverage

    def update(self, coverage: PatientCoverage) -> PatientCoverage:
        current = self.get_active(coverage.patient_id)
        if coverage.active and current is not None and current.id != coverage.id:
            raise ActiveCoverageExistsError(coverage.patient_id)
        coverage.updated_at = utc_now()
        self._rows[coverage.id] = coverage
        return coverage
