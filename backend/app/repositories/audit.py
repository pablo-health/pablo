# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit log repository — interface + in-memory implementation.

Routes never read/write `audit_logs` directly; always go through this
repository. Keeps the PHI-free invariant enforceable in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from ..models.audit import PHI_FIELD_NAMES, AuditLogEntry


class AuditRepository(ABC):
    """Abstract audit log repository."""

    @abstractmethod
    def append(self, entry: AuditLogEntry) -> None:
        """Persist a new audit log entry. Never updates existing entries."""

    @abstractmethod
    def metadata_for_review(self, window_hours: int = 24) -> list[dict]:
        """Return audit rows from the last `window_hours` as plain dicts.

        The result is asserted PHI-free: no field name in PHI_FIELD_NAMES
        ever appears as a key in any row (including nested `changes` dicts).
        Safe to send to an LLM under a GCP BAA via Vertex AI.
        """


class InMemoryAuditRepository(AuditRepository):
    """In-memory audit repository for tests and dev-mode fallback."""

    def __init__(self) -> None:
        self._entries: list[AuditLogEntry] = []

    def append(self, entry: AuditLogEntry) -> None:
        self._entries.append(entry)

    def metadata_for_review(self, window_hours: int = 24) -> list[dict]:
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        rows = [
            asdict(e)
            for e in self._entries
            if datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) >= cutoff
        ]
        _assert_phi_free(rows)
        return rows

    # Test helpers — not part of the interface
    def all(self) -> list[AuditLogEntry]:
        return list(self._entries)


def _assert_phi_free(rows: list[dict]) -> None:
    """Assert no PHI field name appears anywhere in the payload."""
    for row in rows:
        _check_dict(row)


def _check_dict(d: dict) -> None:
    for key, value in d.items():
        if key in PHI_FIELD_NAMES:
            raise AssertionError(
                f"PHI field {key!r} leaked into audit metadata — "
                f"minimum-necessary violation. Fix the caller that wrote this."
            )
        if isinstance(value, dict):
            _check_dict(value)
