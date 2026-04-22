# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform audit log repository — interface + in-memory impl."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.platform_audit import PlatformAuditLogEntry


class PlatformAuditRepository(ABC):
    @abstractmethod
    def append(self, entry: PlatformAuditLogEntry) -> None: ...

    @abstractmethod
    def recent(self, limit: int = 100) -> list[PlatformAuditLogEntry]: ...


class InMemoryPlatformAuditRepository(PlatformAuditRepository):
    def __init__(self) -> None:
        self._entries: list[PlatformAuditLogEntry] = []

    def append(self, entry: PlatformAuditLogEntry) -> None:
        self._entries.append(entry)

    def recent(self, limit: int = 100) -> list[PlatformAuditLogEntry]:
        return list(reversed(self._entries[-limit:]))
