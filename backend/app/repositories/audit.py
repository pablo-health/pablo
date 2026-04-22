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

# Minimum baseline history a user must have before any novelty flag can
# fire for them. Avoids spamming first-week users (their browser
# changes, device switches, and initial patient imports all look novel
# against a one-day baseline). Also protects returning users who were
# away for > baseline_days from getting flagged on re-entry.
MIN_USER_BASELINE_DAYS = 7


class AuditRepository(ABC):
    """Abstract audit log repository."""

    @abstractmethod
    def append(self, entry: AuditLogEntry) -> None:
        """Persist a new audit log entry. Never updates existing entries."""

    @abstractmethod
    def list_for_user(
        self,
        user_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        """Return this user's own audit rows, newest first."""

    @abstractmethod
    def earliest_create_for_patients(
        self, patient_ids: set[str]
    ) -> dict[str, datetime | None]:
        """Return earliest PATIENT_CREATED timestamp per patient_id.

        Used by the review service to suppress care-team checks during
        the new-patient intake window. Cheap: one indexed query, no
        join. Returns None for patient_ids with no PATIENT_CREATED row
        in audit history.
        """

    @abstractmethod
    def metadata_for_review(
        self, window_hours: int = 24, baseline_days: int = DEFAULT_BASELINE_DAYS
    ) -> list[dict]:
        """Return audit rows from the last `window_hours` as plain dicts.

        Each row is enriched with ``is_novel_user_patient``: true when
        this user has >= MIN_USER_BASELINE_DAYS of prior activity AND
        has NOT accessed this patient in the preceding `baseline_days`
        AND did not create that patient in the same window.

        IP and user-agent novelty are intentionally not included — they
        generate too many false positives for legitimate therapists
        (DHCP rotation, mobile / CGNAT, VPN, browser auto-updates,
        multi-device workflows). Raw IP and user_agent are still in the
        row for evidence / forensics; we just don't flag on them.

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

    def list_for_user(
        self,
        user_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        rows = [e for e in self._entries if e.user_id == user_id]
        if since is not None:
            rows = [e for e in rows if _parse_iso(e.timestamp) > since]
        rows.sort(key=lambda e: e.timestamp, reverse=True)
        return rows[:limit]

    def earliest_create_for_patients(
        self, patient_ids: set[str]
    ) -> dict[str, datetime | None]:
        out: dict[str, datetime | None] = dict.fromkeys(patient_ids)
        for e in self._entries:
            if e.action != "patient_created" or not e.patient_id:
                continue
            if e.patient_id not in patient_ids:
                continue
            ts = _parse_iso(e.timestamp)
            current = out[e.patient_id]
            if current is None or ts < current:
                out[e.patient_id] = ts
        return out

    def metadata_for_review(
        self, window_hours: int = 24, baseline_days: int = DEFAULT_BASELINE_DAYS
    ) -> list[dict]:
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=baseline_days)
        min_baseline_cutoff = now - timedelta(days=MIN_USER_BASELINE_DAYS)

        window_rows: list[AuditLogEntry] = []
        baseline_rows: list[AuditLogEntry] = []
        for e in self._entries:
            ts = _parse_iso(e.timestamp)
            if ts >= window_start:
                window_rows.append(e)
            elif ts >= baseline_start:
                baseline_rows.append(e)

        # Users whose earliest audit row predates MIN_USER_BASELINE_DAYS.
        # Only these users get novelty checks — protects first-week users,
        # returning-from-long-absence users, and brand-new installs from
        # spurious flags against a thin baseline.
        earliest_activity: dict[str, datetime] = {}
        for e in self._entries:
            ts = _parse_iso(e.timestamp)
            prev = earliest_activity.get(e.user_id)
            if prev is None or ts < prev:
                earliest_activity[e.user_id] = ts
        users_with_sufficient_baseline = {
            uid for uid, earliest in earliest_activity.items() if earliest < min_baseline_cutoff
        }

        known_user_patient = {
            (e.user_id, e.patient_id) for e in baseline_rows if e.patient_id
        }

        # Same-window creates suppress novelty (user just made the patient).
        created_in_window = {
            (e.user_id, e.patient_id)
            for e in window_rows
            if e.action == "patient_created" and e.patient_id
        }

        out = []
        for e in window_rows:
            row = asdict(e)
            row["is_novel_user_patient"] = bool(
                e.patient_id
                and e.user_id in users_with_sufficient_baseline
                and (e.user_id, e.patient_id) not in known_user_patient
                and (e.user_id, e.patient_id) not in created_in_window
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
