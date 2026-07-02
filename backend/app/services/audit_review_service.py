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
EXPORT_ACTIONS: frozenset[str] = frozenset({"patient_exported", "export_action_taken"})

# Actions that read a patient's chart. For these, the absence of a live
# clinician grant is the "access was not grant-backed" signal — a create
# or delete is excluded because the grant naturally doesn't exist before
# creation or after teardown.
PHI_ACCESS_ACTIONS: frozenset[str] = frozenset(
    {"patient_viewed", "session_viewed", "patient_exported", "export_action_taken"}
)

# Per-row anomaly booleans. A row is "flagged" — and therefore always
# shipped to the model — when any of these is true. Detection is fully
# deterministic; the model only summarises what these flags surface.
ANOMALY_FLAGS: tuple[str, ...] = (
    "is_novel_user_patient",
    "is_same_last_name",
    "is_no_treatment_relationship",
    "is_unauthorized_access",
)

# Hard cap on entries shipped to the model. Flagged rows are always
# included; the remaining budget is filled with the most recent unflagged
# rows for context. The ``summary`` block reports the full volume so the
# model never mistakes an omitted tail for "all clear". Keeps the payload
# (and inference cost / context window) bounded regardless of tenant size.
MAX_MODEL_ENTRIES = 400

# Sensitive actions always worth a human-readable narration even absent a
# hard flag: exports leave the platform, deletes destroy records. Their
# presence alone routes the window to the model.
SENSITIVE_ACTIONS: frozenset[str] = EXPORT_ACTIONS | frozenset({"patient_deleted"})

# Deterministic off-hours band, in UTC. A precise "local night" needs a
# per-tenant timezone we don't carry yet; this conservative deep-night UTC
# window (approx 01:00-06:00 US Eastern) is an approximation that over-includes
# rather than misses. Off-hours rows route to the model (it narrates the
# context); the deterministic clean-report path never *claims* an off-hours
# check, so the approximation can't produce a false "all clear".
OFF_HOURS_UTC_START = 6
OFF_HOURS_UTC_END = 11

# Monthly open-ended discovery pass. The deterministic gate keeps the daily
# run cheap, but it can only catch what we encoded — so on the monthly
# rollup we still send a sample to the model for tenants with real activity,
# even when no flag fired, to catch patterns the flags don't encode.
#
# Policy: ALL SUBSTANTIVE tenants monthly. Any month with at least
# MIN_ENTRIES of activity gets the discovery pass; an empty/near-empty month
# is already covered by the deterministic clean report, so it is skipped. The
# per-call payload is capped (MAX_MODEL_ENTRIES), so cost is bounded and a
# monthly all-tenants pass is a small fraction of the (now deterministic)
# daily run.
#
# ACCESS_RATIO and HIGH_VOLUME are COST LEVERS for scale, off by default
# (ratio 0 ⇒ every substantive month qualifies). If monthly LLM spend ever
# grows material, raise ACCESS_RATIO to narrow to CONCENTRATED-access tenants
# (many audit events per patient — the shape of over-access), and HIGH_VOLUME
# keeps very busy tenants in regardless. Tenants that tripped a deterministic
# flag/aggregate always go to the model anyway, daily or monthly.
MONTHLY_REVIEW_MIN_ENTRIES = 1
MONTHLY_REVIEW_ACCESS_RATIO = 0.0
MONTHLY_REVIEW_HIGH_VOLUME = 2000


@dataclass
class ReviewPayload:
    """Structured payload sent to the review model.

    ``entries`` is the curated, capped set (flagged rows first, then a
    recent unflagged sample). ``summary`` reports the full volume so the
    model can reason about scale without seeing every row and never treats
    an omitted tail as reviewed.
    """

    entries: list[dict]
    user_aggregates: list[dict]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entries": self.entries,
            "user_aggregates": self.user_aggregates,
            "summary": self.summary,
        }


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
        self,
        window_hours: int = 24,
        baseline_days: int = 90,
        internal_actor_user_ids: set[str] | None = None,
        authorized_user_ids: set[str] | None = None,
        review_mode: str = "daily",
    ) -> ReviewPayload:
        """Build the full review payload.

        Detection is fully deterministic: every anomaly the report can
        raise is computed here (per-row flags + per-user aggregates). The
        model's job is to *summarise and prioritise* these signals, not to
        find anomalies in raw rows.

        ``internal_actor_user_ids`` flags traffic from authorized automated
        actors (scheduled internal scans, test/E2E identities). Matching
        entries and aggregates carry ``is_internal_actor=True`` so the
        review model attributes their machine-paced access rather than
        alarming on it for being automated.

        ``authorized_user_ids`` is the set of user_ids that legitimately
        belong to this tenant (its members per the platform roster, plus
        platform admins). When provided, any audit ``user_id`` outside it
        is a structural cross-tenant breach — a user acting in a practice
        that does not employ them — surfaced as a deterministic
        ``foreign_actor`` aggregate. When ``None`` (e.g. a self-hosted
        single-tenant install with no roster), the check is skipped rather
        than guessed.
        """
        internal_actors = internal_actor_user_ids or set()
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
        grant_backed = self._grant_backed_pairs(entries)

        for entry in entries:
            self._enrich_relationships(
                entry,
                user_surnames=user_surnames,
                patient_last_names=patient_last_names,
                patient_created_at=patient_created_at,
                user_appointment_counts=user_appointment_counts,
            )
            # is_internal_actor first: registered automated actors (scans,
            # E2E) are authorized by registration, not by patient_clinicians
            # grants or roster membership, so they're exempt from the
            # unauthorized-access and foreign-actor checks below.
            entry["is_internal_actor"] = entry["user_id"] in internal_actors
            entry["is_unauthorized_access"] = self._is_unauthorized_access(entry, grant_backed)
            entry["is_off_hours"] = self._is_off_hours(entry)

        aggregates = self._compute_user_aggregates(
            entries=entries,
            window_hours=window_hours,
            baseline_days=baseline_days,
        )
        aggregates.extend(self._foreign_actor_alerts(entries, authorized_user_ids, internal_actors))
        for aggregate in aggregates:
            aggregate["is_internal_actor"] = aggregate["user_id"] in internal_actors

        # Deterministic curation: flagged rows (any anomaly, or tied to an
        # aggregate) are always shipped; the rest of the MAX_MODEL_ENTRIES
        # budget is the most recent unflagged rows. ``summary`` carries the
        # full volume so an omitted tail is never mistaken for "all clear".
        summary = self._build_summary(entries, aggregates, review_mode)
        curated = self._curate_entries(entries, aggregates)
        summary["entries_sent"] = len(curated)
        summary["entries_omitted"] = len(entries) - len(curated)

        return ReviewPayload(entries=curated, user_aggregates=aggregates, summary=summary)

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
        if user_appointment_counts.get(entry["user_id"], 0) < MIN_APPOINTMENTS_FOR_CARETEAM_CHECK:
            return False

        # Patient-level suppression: skip new-patient intake window.
        created = patient_created_at.get(patient_id)
        if created is None:
            return False
        if (datetime.now(UTC) - created) < timedelta(days=PATIENT_INTAKE_SUPPRESSION_DAYS):
            return False

        # Now check the actual relationship signal.
        access_ts = _parse_iso(entry["timestamp"])
        has_appointment = self._has_proximate_appointment(entry["user_id"], patient_id, access_ts)
        has_session = self._has_recent_session(entry["user_id"], patient_id, access_ts)
        return not (has_appointment or has_session)

    def _has_proximate_appointment(
        self, user_id: str, patient_id: str, access_ts: datetime
    ) -> bool:
        start = access_ts - timedelta(days=APPOINTMENT_PROXIMITY_DAYS)
        end = access_ts + timedelta(days=APPOINTMENT_PROXIMITY_DAYS)
        starts = self._appointments.start_times_by_patient(patient_id=patient_id, user_id=user_id)
        return any(start <= start_at <= end for start_at in starts)

    def _has_recent_session(self, user_id: str, patient_id: str, access_ts: datetime) -> bool:
        dates = self._sessions.session_dates_by_patient(patient_id, user_id)
        cutoff_lo = access_ts - timedelta(days=1)
        cutoff_hi = access_ts + timedelta(days=1)
        return any(cutoff_lo <= d <= cutoff_hi for d in dates)

    # ---------- authorization (grant-backed access) ----------

    def _grant_backed_pairs(self, entries: list[dict]) -> set[tuple[str, str]]:
        """Return the (user_id, patient_id) pairs — among PHI-access rows —
        that currently have a live clinician grant. Deduped to distinct pairs
        and resolved in a single batched query regardless of row volume."""
        pairs = {
            (e["user_id"], e["patient_id"])
            for e in entries
            if e.get("patient_id") and e.get("action") in PHI_ACCESS_ACTIONS
        }
        return self._patients.live_grant_pairs(pairs)

    def _is_unauthorized_access(self, entry: dict, grant_backed: set[tuple[str, str]]) -> bool:
        """True when a chart-read action has no live clinician grant behind
        it. Approximate current-state (the grant table keeps no history),
        so a grant revoked since the access reads as unauthorized — which
        is the conservative direction for a review signal."""
        if entry.get("is_internal_actor"):
            return False
        patient_id = entry.get("patient_id")
        if not patient_id or entry.get("action") not in PHI_ACCESS_ACTIONS:
            return False
        return (entry["user_id"], patient_id) not in grant_backed

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

    def _export_rate_alerts(self, window_hours: int, baseline_days: int) -> list[dict]:
        # Pull a wider audit slice (window + baseline) once to compute
        # both today's count and the historical distribution per user.
        wide_window_hours = window_hours + baseline_days * 24
        wide = self._audit.metadata_for_review(window_hours=wide_window_hours)

        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)
        warmup_cutoff = now - timedelta(days=MIN_BASELINE_DAYS_FOR_EXPORT_RATE)

        per_user_daily_exports: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
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

    def _foreign_actor_alerts(
        self,
        entries: list[dict],
        authorized_user_ids: set[str] | None,
        internal_actors: set[str],
    ) -> list[dict]:
        """Deterministic cross-tenant breach detector.

        A ``user_id`` in this tenant's audit log that is not in the tenant's
        authorized roster (its members per the platform mapping, plus
        platform admins) is a user acting in a practice that does not employ
        them — the structural cross-tenant signal. This is decided in code,
        not by the model, which only ever sees one tenant's logs and cannot
        judge it. Skipped entirely when no roster is supplied (a self-hosted
        single-tenant install), so it never guesses. Registered internal
        actors are authorized by registration and never count as foreign.
        """
        if authorized_user_ids is None:
            return []
        allowed = authorized_user_ids | internal_actors
        counts: Counter[str] = Counter(e["user_id"] for e in entries)
        return [
            {
                "user_id": uid,
                "alert": "foreign_actor",
                "count": count,
                "detail": "user_id not in this tenant's authorized roster",
            }
            for uid, count in counts.items()
            if uid not in allowed
        ]

    # ---------- deterministic triage: route to the model only when needed ----------

    def _is_off_hours(self, entry: dict) -> bool:
        ts = _parse_iso(entry["timestamp"])
        return OFF_HOURS_UTC_START <= ts.hour < OFF_HOURS_UTC_END

    def _is_interesting(self, entry: dict) -> bool:
        """A row is "interesting" — not provably routine — when it trips any
        anomaly flag, lands off-hours, or is a sensitive (export/delete)
        action. Interesting rows route the window to the model and are
        always shipped; everything else is routine read/create traffic the
        deterministic pass already vouched for."""
        return (
            any(entry.get(flag) for flag in ANOMALY_FLAGS)
            or bool(entry.get("is_off_hours"))
            or entry.get("action") in SENSITIVE_ACTIONS
        )

    def _curate_entries(self, entries: list[dict], aggregates: list[dict]) -> list[dict]:
        """Ship interesting rows (always) + a recent routine sample up to the
        cap. Rows tied to a user_aggregate are interesting too — an
        aggregate without its rows would be unexplained in the report."""
        flagged_users = {a["user_id"] for a in aggregates}
        interesting = [
            e for e in entries if self._is_interesting(e) or e["user_id"] in flagged_users
        ]
        routine = [
            e for e in entries if not (self._is_interesting(e) or e["user_id"] in flagged_users)
        ]
        budget = max(0, MAX_MODEL_ENTRIES - len(interesting))
        # entries arrive oldest-first; keep the most recent routine rows.
        sample = routine[-budget:] if budget else []
        # interesting first (capped too, in the pathological all-flagged case).
        return interesting[:MAX_MODEL_ENTRIES] + sample

    def _build_summary(
        self, entries: list[dict], aggregates: list[dict], review_mode: str
    ) -> dict[str, Any]:
        action_counts: Counter[str] = Counter(e.get("action", "?") for e in entries)
        flagged = [e for e in entries if self._is_interesting(e)]
        total = len(entries)
        distinct_patients = len({e["patient_id"] for e in entries if e.get("patient_id")})
        entries_per_patient = total / max(distinct_patients, 1)

        # A deterministic anomaly always routes to the model (daily or
        # monthly). Absent one, the monthly rollup still sends the active
        # tenants for open-ended discovery (see the MONTHLY_REVIEW_* notes).
        reason = "none"
        if flagged or aggregates:
            reason = "anomaly"
        elif (
            review_mode == "monthly"
            and total >= MONTHLY_REVIEW_MIN_ENTRIES
            and (
                entries_per_patient >= MONTHLY_REVIEW_ACCESS_RATIO
                or total >= MONTHLY_REVIEW_HIGH_VOLUME
            )
        ):
            reason = "monthly_discovery"

        return {
            "total_entries": total,
            "distinct_users": len({e["user_id"] for e in entries}),
            "distinct_patients": distinct_patients,
            "entries_per_patient": round(entries_per_patient, 1),
            "counts_by_action": dict(action_counts),
            "flagged_entries": len(flagged),
            "aggregate_alerts": len(aggregates),
            # The gate. False means: no deterministic anomaly AND (daily, or a
            # monthly month too quiet/sparse for discovery to add anything) —
            # so the window is classified "fine" in code and never reaches
            # the model. ``model_review_reason`` records why when True.
            "needs_model_review": reason != "none",
            "model_review_reason": reason,
        }

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
                last_name = self._patients.get_last_name(pid, uid)
                if last_name is not None:
                    out[pid] = last_name.strip().lower() or None
                    break
            else:
                out[pid] = None
        return out

    def _patient_created_at(self, patient_ids: set[str]) -> dict[str, datetime | None]:
        """Patient creation timestamps from the audit log itself.

        Avoids a join to the patients table — the audit log already
        records PATIENT_CREATED, and the repo method is a single
        indexed lookup (~ms even at high-tenant-count scale).
        """
        return self._audit.earliest_create_for_patients(patient_ids)

    def _user_total_appointment_counts(self, user_ids: set[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        # Far-past lookback — total appointments ever scheduled for this user.
        far_past = datetime.now(UTC) - timedelta(days=365 * 5)
        far_future = datetime.now(UTC) + timedelta(days=365)
        for uid in user_ids:
            out[uid] = self._appointments.count_by_range(
                user_id=uid, start=far_past, end=far_future
            )
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
