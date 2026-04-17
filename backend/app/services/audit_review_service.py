# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit review payload composition for the daily HIPAA log-review job.

Sits on top of the audit repository and joins to patient / user /
appointment data to compute behavioral signals that audit data alone
can't surface:

  - is_same_last_name: user.name and patient.last_name share a surname
  - is_no_treatment_relationship: PATIENT_VIEWED with no scheduled
    appointment or finalized session in a reasonable window — and
    the patient isn't brand-new (intake suppressed)

Also computes per-user aggregates that don't fit the per-row shape:

  - bulk_delete: too many PATIENT_DELETED actions in the window
  - high_export_rate: today's export count exceeds the user's P95
    over the baseline window

Returns a single dict the LLM job ships to Vertex.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..repositories.audit import AuditRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.session import TherapySessionRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.repositories.appointment import AppointmentRepository


# -- thresholds (tunable; doc'd in the SYSTEM_PROMPT) --

# Care-team check is suppressed for this many days after a patient is
# created. New-patient intake naturally has no appointments yet.
PATIENT_INTAKE_SUPPRESSION_DAYS = 14

# Care-team check requires the user to have at least this many past
# appointments. Otherwise the system is too cold to differentiate
# "no appointment" (real signal) from "no appointments yet anywhere"
# (warmup noise).
MIN_APPOINTMENTS_FOR_CARETEAM_CHECK = 5

# Window in days around an audit row to count an appointment as
# "supporting" the access.
APPOINTMENT_PROXIMITY_DAYS = 7

# Bulk-delete trigger: more than this many PATIENT_DELETED events from
# one user in the window.
BULK_DELETE_THRESHOLD = 3

# Export-rate baseline must have at least this many days of data
# before we trust a P95 calculation against it.
MIN_BASELINE_DAYS_FOR_EXPORT_RATE = 14

# Audit actions that count as "exports" for the rate alert.
EXPORT_ACTIONS: frozenset[str] = frozenset(
    {"patient_exported", "export_action_taken"}
)


@dataclass
class ReviewPayload:
    """Structured payload sent to Claude for the daily review."""

    entries: list[dict]
    user_aggregates: list[dict]

    def to_dict(self) -> dict[str, Any]:
        return {"entries": self.entries, "user_aggregates": self.user_aggregates}


class AuditReviewService:
    """Composes the daily HIPAA-review payload from multiple data sources."""

    def __init__(
        self,
        audit_repo: AuditRepository,
        patient_repo: PatientRepository,
        user_repo: UserRepository,
        appointment_repo: AppointmentRepository,
        session_repo: TherapySessionRepository,
    ) -> None:
        self._audit = audit_repo
        self._patients = patient_repo
        self._users = user_repo
        self._appointments = appointment_repo
        self._sessions = session_repo

    def compute_payload(
        self, window_hours: int = 24, baseline_days: int = 90
    ) -> ReviewPayload:
        """Build the full review payload."""
        entries = self._audit.metadata_for_review(
            window_hours=window_hours, baseline_days=baseline_days
        )

        # Build per-user surname map and per-patient last_name + created_at
        # context. Names never leave this method — only booleans land in
        # the payload.
        unique_user_ids = {e["user_id"] for e in entries}
        unique_patient_ids = {e["patient_id"] for e in entries if e.get("patient_id")}

        user_surnames = self._user_surnames(unique_user_ids)
        patient_last_names = self._patient_last_names(unique_user_ids, unique_patient_ids)
        patient_created_at = self._patient_created_at(unique_patient_ids)
        user_appointment_counts = self._user_total_appointment_counts(unique_user_ids)

        for entry in entries:
            self._enrich_relationships(
                entry,
                user_surnames=user_surnames,
                patient_last_names=patient_last_names,
                patient_created_at=patient_created_at,
                user_appointment_counts=user_appointment_counts,
            )

        aggregates = self._compute_user_aggregates(
            entries=entries,
            window_hours=window_hours,
            baseline_days=baseline_days,
        )

        return ReviewPayload(entries=entries, user_aggregates=aggregates)

    # ---------- per-row enrichment ----------

    def _enrich_relationships(
        self,
        entry: dict,
        *,
        user_surnames: dict[str, str | None],
        patient_last_names: dict[str, str | None],
        patient_created_at: dict[str, datetime | None],
        user_appointment_counts: dict[str, int],
    ) -> None:
        patient_id = entry.get("patient_id")
        user_id = entry["user_id"]

        # Same-last-name: cheap and worth flagging regardless of whether
        # the patient is new or the user just signed up — a relative
        # being seen IS the signal we want surfaced for human review.
        entry["is_same_last_name"] = bool(
            patient_id
            and user_surnames.get(user_id)
            and patient_last_names.get(patient_id)
            and user_surnames[user_id] == patient_last_names[patient_id]
        )

        # Care-team / no-treatment-relationship: only fires for VIEW
        # actions on established patients in a system with enough
        # appointment history. Suppressed otherwise.
        entry["is_no_treatment_relationship"] = self._has_no_treatment_relationship(
            entry,
            patient_created_at=patient_created_at,
            user_appointment_counts=user_appointment_counts,
        )

    def _has_no_treatment_relationship(
        self,
        entry: dict,
        *,
        patient_created_at: dict[str, datetime | None],
        user_appointment_counts: dict[str, int],
    ) -> bool:
        if entry.get("action") not in {"patient_viewed", "session_viewed"}:
            return False

        patient_id = entry.get("patient_id")
        if not patient_id:
            return False

        # System-level warmup: don't fire until the user has enough
        # appointment history that "no appointment" actually means
        # something.
        if (
            user_appointment_counts.get(entry["user_id"], 0)
            < MIN_APPOINTMENTS_FOR_CARETEAM_CHECK
        ):
            return False

        # Patient-level suppression: skip new-patient intake window.
        created = patient_created_at.get(patient_id)
        if created is None:
            return False
        if (datetime.now(UTC) - created) < timedelta(
            days=PATIENT_INTAKE_SUPPRESSION_DAYS
        ):
            return False

        # Now check the actual relationship signal.
        access_ts = _parse_iso(entry["timestamp"])
        has_appointment = self._has_proximate_appointment(
            entry["user_id"], patient_id, access_ts
        )
        has_session = self._has_recent_session(
            entry["user_id"], patient_id, access_ts
        )
        return not (has_appointment or has_session)

    def _has_proximate_appointment(
        self, user_id: str, patient_id: str, access_ts: datetime
    ) -> bool:
        start = access_ts - timedelta(days=APPOINTMENT_PROXIMITY_DAYS)
        end = access_ts + timedelta(days=APPOINTMENT_PROXIMITY_DAYS)
        appts = self._appointments.list_by_patient(
            patient_id=patient_id, user_id=user_id
        )
        return any(start <= appt.start_at <= end for appt in appts)

    def _has_recent_session(
        self, user_id: str, patient_id: str, access_ts: datetime
    ) -> bool:
        sessions = self._sessions.list_by_patient(patient_id, user_id)
        cutoff_lo = access_ts - timedelta(days=1)
        cutoff_hi = access_ts + timedelta(days=1)
        return any(cutoff_lo <= s.session_date <= cutoff_hi for s in sessions)

    # ---------- per-user aggregates ----------

    def _compute_user_aggregates(
        self, entries: list[dict], window_hours: int, baseline_days: int
    ) -> list[dict]:
        out: list[dict] = []

        # Bulk-delete: count PATIENT_DELETED in the window per user.
        delete_counts: Counter[str] = Counter()
        for e in entries:
            if e.get("action") == "patient_deleted":
                delete_counts[e["user_id"]] += 1
        for user_id, count in delete_counts.items():
            if count > BULK_DELETE_THRESHOLD:
                out.append(
                    {
                        "user_id": user_id,
                        "alert": "bulk_delete",
                        "count": count,
                        "threshold": BULK_DELETE_THRESHOLD,
                        "window_hours": window_hours,
                    }
                )

        # Export-rate alert: per-user count of EXPORT_ACTIONS in the
        # window, compared against P95 of the user's daily export count
        # over the baseline window.
        out.extend(self._export_rate_alerts(window_hours, baseline_days))
        return out

    def _export_rate_alerts(
        self, window_hours: int, baseline_days: int
    ) -> list[dict]:
        # Pull a wider audit slice (window + baseline) once to compute
        # both today's count and the historical distribution per user.
        wide_window_hours = window_hours + baseline_days * 24
        wide = self._audit.metadata_for_review(window_hours=wide_window_hours)

        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)
        warmup_cutoff = now - timedelta(days=MIN_BASELINE_DAYS_FOR_EXPORT_RATE)

        per_user_daily_exports: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        per_user_window_exports: Counter[str] = Counter()
        per_user_first_seen: dict[str, datetime] = {}

        for row in wide:
            uid = row["user_id"]
            ts = _parse_iso(row["timestamp"])
            prev = per_user_first_seen.get(uid)
            if prev is None or ts < prev:
                per_user_first_seen[uid] = ts
            if row["action"] not in EXPORT_ACTIONS:
                continue
            if ts >= window_start:
                per_user_window_exports[uid] += 1
            else:
                day = ts.date().isoformat()
                per_user_daily_exports[uid][day] += 1

        alerts: list[dict] = []
        for uid, today_count in per_user_window_exports.items():
            if today_count == 0:
                continue
            # System-level warmup: user must have been active for at
            # least MIN_BASELINE_DAYS_FOR_EXPORT_RATE calendar days.
            first_seen = per_user_first_seen.get(uid)
            if first_seen is None or first_seen > warmup_cutoff:
                continue
            counts = list(per_user_daily_exports[uid].values()) or [0]
            p95 = _percentile(counts, 95)
            # Require today to exceed P95 AND at least doubled it, with
            # a minimum absolute floor of 2 exports to avoid firing on
            # "user with zero historical exports did 1 today."
            if today_count > p95 and today_count >= max(2, 2 * p95):
                alerts.append(
                    {
                        "user_id": uid,
                        "alert": "high_export_rate",
                        "count": today_count,
                        "p95": p95,
                        "baseline_export_days": len(per_user_daily_exports[uid]),
                    }
                )
        return alerts

    # ---------- repo lookups ----------

    def _user_surnames(self, user_ids: set[str]) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for uid in user_ids:
            user = self._users.get(uid)
            out[uid] = _extract_surname(user.name) if user else None
        return out

    def _patient_last_names(
        self, user_ids: set[str], patient_ids: set[str]
    ) -> dict[str, str | None]:
        """Patient last_name lookup. Patient repo enforces tenant scoping
        via user_id — we resolve each patient against any user that touched
        it. (For the solo deploy this is just the one therapist.)"""
        out: dict[str, str | None] = {}
        for pid in patient_ids:
            for uid in user_ids:
                patient = self._patients.get(pid, uid)
                if patient:
                    out[pid] = (patient.last_name or "").strip().lower() or None
                    break
            else:
                out[pid] = None
        return out

    def _patient_created_at(
        self, patient_ids: set[str]
    ) -> dict[str, datetime | None]:
        """Patient creation timestamps from the audit log itself.

        Avoids a join to the patients table — the audit log already
        records PATIENT_CREATED, and the repo method is a single
        indexed lookup (~ms even at SaaS scale).
        """
        return self._audit.earliest_create_for_patients(patient_ids)

    def _user_total_appointment_counts(self, user_ids: set[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        # Far-past lookback — total appointments ever scheduled for this user.
        far_past = datetime.now(UTC) - timedelta(days=365 * 5)
        far_future = datetime.now(UTC) + timedelta(days=365)
        for uid in user_ids:
            appts = self._appointments.list_by_range(
                user_id=uid, start=far_past, end=far_future
            )
            out[uid] = len(appts)
        return out


# ---------- helpers ----------


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _extract_surname(full_name: str | None) -> str | None:
    """Heuristic last-name extraction. Splits on whitespace, takes the
    last token, lowercased. Good enough for v1; falls back to None if
    the name is empty."""
    if not full_name:
        return None
    tokens = full_name.strip().split()
    if not tokens:
        return None
    return tokens[-1].lower()


def _percentile(values: list[int], pct: float) -> float:
    """Simple percentile — uses statistics.quantiles for n>=2 else max."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    quantiles = statistics.quantiles(values, n=100, method="inclusive")
    # quantiles returns 99 cut points (1..99); P95 is index 94.
    return quantiles[int(pct) - 1]
