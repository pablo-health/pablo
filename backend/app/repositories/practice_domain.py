# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Repository abstraction for the hosts a practice serves from.

The table is in the shared ``platform`` schema (no RLS): the ``practice_id``
argument on every write is the access-control boundary. ``get`` is deliberately
unscoped — a host belongs to exactly one practice, and the service has to see
whose it is to tell "already yours" from "belongs to someone else".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from threading import Lock
from typing import TYPE_CHECKING

from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models.practice_domain import DomainPurpose, PracticeDomain


class DomainTakenError(Exception):
    """The host is already recorded, for this practice or another."""


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


class InMemoryPracticeDomainRepository(PracticeDomainRepository):
    """In-memory implementation for tests."""

    def __init__(self) -> None:
        self._rows: dict[str, PracticeDomain] = {}
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

    def put(self, domain: PracticeDomain) -> None:
        """Test seam: record a row as-is, status and all."""
        with self._lock:
            self._rows[domain.domain] = replace(domain)


def get_practice_domain_repository() -> PracticeDomainRepository:
    """The request-scoped Postgres repository."""
    from ..db import get_db_session  # noqa: PLC0415 — needs a request in flight
    from .postgres.practice_domain import PostgresPracticeDomainRepository  # noqa: PLC0415

    return PostgresPracticeDomainRepository(get_db_session())
