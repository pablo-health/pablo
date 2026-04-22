# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform audit log repository — interface + in-memory impl.

Platform audit is a cross-tenant administrative stream. Routes that
provision tenants, flip flags, or edit allowlists go through this
repository so the "who did what, when" record lives in one place and
can be granted to auditors independently of per-tenant audit logs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.platform_audit import PlatformAuditLogEntry


class PlatformAuditRepository(ABC):
    """Abstract platform audit log repository."""

    @abstractmethod
    def append(self, entry: PlatformAuditLogEntry) -> None:
        """Persist a new platform audit log entry. Append-only."""

    @abstractmethod
    def recent(self, limit: int = 100) -> list[PlatformAuditLogEntry]:
        """Return the most recent entries, newest first."""


class InMemoryPlatformAuditRepository(PlatformAuditRepository):
    """In-memory implementation used by tests and dev harnesses."""

    def __init__(self) -> None:
        self._entries: list[PlatformAuditLogEntry] = []

    def append(self, entry: PlatformAuditLogEntry) -> None:
        self._entries.append(entry)

    def recent(self, limit: int = 100) -> list[PlatformAuditLogEntry]:
        return list(reversed(self._entries[-limit:]))
