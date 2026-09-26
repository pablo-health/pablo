# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice note-type repository — the note formats a practice defined itself.

One row per saved version. Saving a type writes the next version rather
than editing the last, so a note generated from version 2 can still be
rendered against version 2's fields after version 3 exists. Retiring stamps
``retired_at`` on the latest version; saving again writes a fresh, active
version.

Nothing here belongs to a patient or a clinician, so there is no
``has_patient_access`` call and no ``user_id`` predicate: the session is
already pointed at one practice's schema, and every query is scoped by
being on it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True)
class StoredNoteType:
    """One saved version of a practice note type."""

    key: str
    version: int
    definition: dict[str, Any]
    created_by: str
    created_at: datetime
    retired_at: datetime | None = None


class PracticeNoteTypeRepository(ABC):
    """Abstract base class for practice note-type data access."""

    @abstractmethod
    def get(self, key: str, version: int | None = None) -> StoredNoteType | None:
        """One version, or the latest when ``version`` is None."""

    @abstractmethod
    def list_latest(self) -> list[StoredNoteType]:
        """The latest version of every key, retired or not, ordered by key."""

    @abstractmethod
    def add_version(
        self,
        key: str,
        definition: dict[str, Any],
        created_by: str,
        created_at: datetime,
    ) -> StoredNoteType:
        """Write the next version of ``key`` (version 1 for a new key)."""

    @abstractmethod
    def retire(self, key: str, retired_at: datetime) -> StoredNoteType | None:
        """Stamp the latest version retired. ``None`` if the key is unknown."""


class InMemoryPracticeNoteTypeRepository(PracticeNoteTypeRepository):
    """In-memory implementation for tests and local runs."""

    def __init__(self) -> None:
        self._rows: list[StoredNoteType] = []

    def get(self, key: str, version: int | None = None) -> StoredNoteType | None:
        rows = [r for r in self._rows if r.key == key]
        if version is not None:
            rows = [r for r in rows if r.version == version]
        return max(rows, key=lambda r: r.version, default=None)

    def list_latest(self) -> list[StoredNoteType]:
        latest = [self.get(key) for key in sorted({r.key for r in self._rows})]
        return [r for r in latest if r is not None]

    def add_version(
        self,
        key: str,
        definition: dict[str, Any],
        created_by: str,
        created_at: datetime,
    ) -> StoredNoteType:
        current = self.get(key)
        stored = StoredNoteType(
            key=key,
            version=(current.version + 1) if current else 1,
            definition=definition,
            created_by=created_by,
            created_at=created_at,
        )
        self._rows.append(stored)
        return stored

    def retire(self, key: str, retired_at: datetime) -> StoredNoteType | None:
        current = self.get(key)
        if current is None:
            return None
        retired = replace(current, retired_at=retired_at)
        self._rows[self._rows.index(current)] = retired
        return retired
