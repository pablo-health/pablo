# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit log repository — interface + in-memory implementation.

Routes never read/write `audit_logs` directly; always go through this
repository. Keeps the PHI-free invariant enforceable in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ..models.audit import PHI_FIELD_NAMES

if TYPE_CHECKING:
    from ..models.audit import AuditLogEntry


# Default historical-baseline window used to classify "novel" pairs in
# metadata_for_review(). 90d matches the prompt's "no prior access in
# 90d" checkpoint. Configurable per-call.
DEFAULT_BASELINE_DAYS = 90


class AuditRepository(ABC):
    """Abstract audit log repository."""

    @abstractmethod
    def append(self, entry: AuditLogEntry) -> None:
        """Persist a new audit log entry. Never updates existing entries."""

    @abstractmethod
    def metadata_for_review(
        self, window_hours: int = 24, baseline_days: int = DEFAULT_BASELINE_DAYS
    ) -> list[dict]:
        """Return audit rows from the last `window_hours` as plain dicts.

        Each row is enriched with boolean flags that compare against a
        preceding `baseline_days` history so the reviewer can spot
        first-time patterns without the caller shipping 90 days of raw
        audit data to an LLM:

          - ``is_novel_user_patient``: (user_id, patient_id) never seen
            in the baseline window.
          - ``is_novel_user_ip``: (user_id, ip_address) never seen in
            the baseline window.
          - ``is_novel_user_agent``: (user_id, user_agent) never seen
            in the baseline window.

        The result is asserted PHI-free: no field name in
        PHI_FIELD_NAMES ever appears as a key in any row (including
        nested `changes` dicts). Safe to send to an LLM under a GCP BAA
        via Vertex AI.
        """


class InMemoryAuditRepository(AuditRepository):
    """In-memory audit repository for tests and dev-mode fallback."""

    def __init__(self) -> None:
        self._entries: list[AuditLogEntry] = []

    def append(self, entry: AuditLogEntry) -> None:
        self._entries.append(entry)

    def metadata_for_review(
        self, window_hours: int = 24, baseline_days: int = DEFAULT_BASELINE_DAYS
    ) -> list[dict]:
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=baseline_days)

        window_rows: list[AuditLogEntry] = []
        baseline_rows: list[AuditLogEntry] = []
        for e in self._entries:
            ts = _parse_iso(e.timestamp)
            if ts >= window_start:
                window_rows.append(e)
            elif ts >= baseline_start:
                baseline_rows.append(e)

        known_user_patient = {
            (e.user_id, e.patient_id) for e in baseline_rows if e.patient_id
        }
        known_user_ip = {(e.user_id, e.ip_address) for e in baseline_rows if e.ip_address}
        known_user_agent = {
            (e.user_id, e.user_agent) for e in baseline_rows if e.user_agent
        }

        out = []
        for e in window_rows:
            row = asdict(e)
            row["is_novel_user_patient"] = bool(
                e.patient_id and (e.user_id, e.patient_id) not in known_user_patient
            )
            row["is_novel_user_ip"] = bool(
                e.ip_address and (e.user_id, e.ip_address) not in known_user_ip
            )
            row["is_novel_user_agent"] = bool(
                e.user_agent and (e.user_id, e.user_agent) not in known_user_agent
            )
            out.append(row)

        _assert_phi_free(out)
        return out

    # Test helpers — not part of the interface
    def all(self) -> list[AuditLogEntry]:
        return list(self._entries)


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


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
