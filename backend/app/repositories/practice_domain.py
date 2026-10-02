# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for the hosts a practice serves from, and the
registrable domains they sit under.

The tables are in the shared ``platform`` schema (no RLS): the ``practice_id``
argument on every write is the access-control boundary. ``get`` and
``get_apex`` are deliberately unscoped — a host or domain belongs to exactly
one practice, and the service has to see whose it is to tell "already yours"
from "belongs to someone else".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.practice_domain import (
        DomainPurpose,
        EmailIdentityStatus,
        HostStatus,
        PracticeDomain,
        PracticeDomainApex,
        ServingState,
    )


class DomainTakenError(Exception):
    """The host or domain is already recorded, for this practice or another."""


class PracticeDomainRepository(ABC):
    @abstractmethod
    def get(self, domain: str) -> PracticeDomain | None:
        """The row for this host, whichever practice holds it."""

    @abstractmethod
    def list_for_practice(self, practice_id: str) -> list[PracticeDomain]:
        """Every host this practice holds, oldest first."""

    @abstractmethod
    def add(self, domain: PracticeDomain) -> PracticeDomain:
        """Insert a host.

        Raises:
            DomainTakenError: the host is already recorded.
        """

    @abstractmethod
    def remove(self, domain: str, practice_id: str) -> bool:
        """Delete this practice's host. Returns whether a row existed."""

    @abstractmethod
    def set_primary(self, domain: str, practice_id: str, purpose: DomainPurpose) -> None:
        """Make this host the practice's primary for its purpose, unsetting any other."""

    @abstractmethod
    def get_apex(self, apex: str) -> PracticeDomainApex | None:
        """The row for this registrable domain, whichever practice holds it."""

    @abstractmethod
    def list_apexes_for_practice(self, practice_id: str) -> list[PracticeDomainApex]:
        """Every registrable domain this practice holds."""

    @abstractmethod
    def add_apex(self, apex: PracticeDomainApex) -> PracticeDomainApex:
        """Insert a registrable domain.

        Raises:
            DomainTakenError: the domain is already recorded.
        """

    @abstractmethod
    def remove_apex(self, apex: str, practice_id: str) -> bool:
        """Delete this practice's registrable domain. Returns whether a row existed."""

    @abstractmethod
    def mark_apex_verified(self, apex: str, practice_id: str, at: datetime) -> None:
        """Record that the domain's ownership record was found at *at*."""

    # --- Serving the hosts (app.services.practice_domain_reconciler) ---------

    @abstractmethod
    def list_all(self) -> list[PracticeDomain]:
        """Every host of every practice, ``removing`` ones included, oldest first."""

    @abstractmethod
    def mark_removing(self, domain: str, practice_id: str) -> bool:
        """Set this practice's host ``removing`` and no longer primary.

        Returns whether a row changed.
        """

    @abstractmethod
    def record_serving(
        self,
        domain: str,
        *,
        expected_status: HostStatus,
        state: ServingState,
    ) -> bool:
        """Write what serving the host found, only if its status is still
        *expected_status*.

        The guard is what keeps a removal the practice made while the job was
        working from being overwritten. Returns whether the row was written.
        """

    @abstractmethod
    def claim_reissue(self, domain: str, *, last: datetime | None, at: datetime) -> bool:
        """Record that the host's certificate is being requested again at *at*,
        only if the last time recorded is still *last*.

        Two runs at once both see a stuck certificate; this is what lets only
        one of them request it again. Returns whether this one may.
        """

    @abstractmethod
    def delete_removing(self, domain: str) -> bool:
        """Delete the host if it is ``removing``. Returns whether a row went."""

    @abstractmethod
    def set_email_identity(
        self,
        apex: str,
        practice_id: str,
        status: EmailIdentityStatus,
        dkim_tokens: list[str] | None,
    ) -> None:
        """Record the state and DKIM tokens of the domain's email sending identity."""

    # --- How long a host has waited (PracticeDomainService.check) -----------

    @abstractmethod
    def mark_records_complete(self, domain: str, practice_id: str, at: datetime) -> bool:
        """Record that every record the host needs was found at *at*, unless
        that is already recorded. Returns whether the row was written."""

    @abstractmethod
    def clear_records_complete(self, domain: str, practice_id: str) -> bool:
        """Forget when the host's records were complete, and any report of its
        wait since. Returns whether the row was written."""

    @abstractmethod
    def claim_stuck_report(self, domain: str, practice_id: str, at: datetime) -> bool:
        """Record that the host's wait is being reported at *at*, only if it has
        not been since its records were complete. Returns whether this caller
        is the one to report it."""


class InMemoryPracticeDomainRepository(PracticeDomainRepository):
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, PracticeDomain] = {}
        self._apexes: dict[str, PracticeDomainApex] = {}
        self._lock = Lock()

    def get(self, domain: str) -> PracticeDomain | None:
        with self._lock:
            row = self._rows.get(domain)
            return replace(row) if row else None

    def list_for_practice(self, practice_id: str) -> list[PracticeDomain]:
        with self._lock:
            rows = [replace(r) for r in self._rows.values() if r.practice_id == practice_id]
        return sorted(rows, key=lambda r: (r.created_at, r.domain))

    def add(self, domain: PracticeDomain) -> PracticeDomain:
        with self._lock:
            if domain.domain in self._rows:
                raise DomainTakenError(domain.domain)
            self._rows[domain.domain] = replace(domain)
        return domain

    def remove(self, domain: str, practice_id: str) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.practice_id != practice_id:
                return False
            del self._rows[domain]
            return True

    def set_primary(self, domain: str, practice_id: str, purpose: DomainPurpose) -> None:
        now = utc_now()
        with self._lock:
            for row in self._rows.values():
                if row.practice_id == practice_id and row.purpose == purpose:
                    row.is_primary = row.domain == domain
                    row.updated_at = now

    def get_apex(self, apex: str) -> PracticeDomainApex | None:
        with self._lock:
            row = self._apexes.get(apex)
            return replace(row) if row else None

    def list_apexes_for_practice(self, practice_id: str) -> list[PracticeDomainApex]:
        with self._lock:
            rows = [replace(r) for r in self._apexes.values() if r.practice_id == practice_id]
        return sorted(rows, key=lambda r: r.apex)

    def add_apex(self, apex: PracticeDomainApex) -> PracticeDomainApex:
        with self._lock:
            if apex.apex in self._apexes:
                raise DomainTakenError(apex.apex)
            self._apexes[apex.apex] = replace(apex)
        return apex

    def remove_apex(self, apex: str, practice_id: str) -> bool:
        with self._lock:
            row = self._apexes.get(apex)
            if row is None or row.practice_id != practice_id:
                return False
            del self._apexes[apex]
            return True

    def mark_apex_verified(self, apex: str, practice_id: str, at: datetime) -> None:
        with self._lock:
            row = self._apexes.get(apex)
            if row is not None and row.practice_id == practice_id:
                row.verified_at = at
                row.updated_at = at

    def list_all(self) -> list[PracticeDomain]:
        with self._lock:
            rows = [replace(r) for r in self._rows.values()]
        return sorted(rows, key=lambda r: (r.created_at, r.domain))

    def mark_removing(self, domain: str, practice_id: str) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.practice_id != practice_id:
                return False
            row.status = "removing"
            row.is_primary = False
            row.updated_at = utc_now()
            return True

    def record_serving(
        self,
        domain: str,
        *,
        expected_status: HostStatus,
        state: ServingState,
    ) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.status != expected_status:
                return False
            row.status = state.status
            row.cert_auth_value = state.cert_auth_value
            row.cert_status = state.cert_status
            row.last_error = state.last_error
            row.cert_reissued_at = state.cert_reissued_at
            row.verified_at = state.verified_at
            if state.status == "active":
                row.records_complete_at = None
                row.stuck_reported_at = None
            row.updated_at = utc_now()
            return True

    def claim_reissue(self, domain: str, *, last: datetime | None, at: datetime) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.cert_reissued_at != last:
                return False
            row.cert_reissued_at = at
            return True

    def delete_removing(self, domain: str) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.status != "removing":
                return False
            del self._rows[domain]
            return True

    def set_email_identity(
        self,
        apex: str,
        practice_id: str,
        status: EmailIdentityStatus,
        dkim_tokens: list[str] | None,
    ) -> None:
        with self._lock:
            row = self._apexes.get(apex)
            if row is not None and row.practice_id == practice_id:
                row.email_identity_status = status
                row.email_dkim_tokens = list(dkim_tokens) if dkim_tokens else None
                row.updated_at = utc_now()

    def mark_records_complete(self, domain: str, practice_id: str, at: datetime) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.practice_id != practice_id or row.records_complete_at:
                return False
            row.records_complete_at = at
            return True

    def clear_records_complete(self, domain: str, practice_id: str) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.practice_id != practice_id:
                return False
            if row.records_complete_at is None and row.stuck_reported_at is None:
                return False
            row.records_complete_at = None
            row.stuck_reported_at = None
            return True

    def claim_stuck_report(self, domain: str, practice_id: str, at: datetime) -> bool:
        with self._lock:
            row = self._rows.get(domain)
            if row is None or row.practice_id != practice_id or row.stuck_reported_at:
                return False
            row.stuck_reported_at = at
            return True

    def put(self, domain: PracticeDomain) -> None:
        """Test seam: record a row as-is, status and all."""
        with self._lock:
            self._rows[domain.domain] = replace(domain)

    def put_apex(self, apex: PracticeDomainApex) -> None:
        """Test seam: record a registrable domain as-is."""
        with self._lock:
            self._apexes[apex.apex] = replace(apex)


def get_practice_domain_repository() -> PracticeDomainRepository:
    """The request-scoped Postgres repository."""
    from ..db import get_db_session  # noqa: PLC0415 — needs a request in flight
    from .postgres.practice_domain import PostgresPracticeDomainRepository  # noqa: PLC0415

    return PostgresPracticeDomainRepository(get_db_session())
