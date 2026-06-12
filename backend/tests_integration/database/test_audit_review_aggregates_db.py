# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Per-user aggregates (``user_aggregates``) against real Postgres.

Two alert families come out of ``AuditReviewService._compute_user_aggregates``:

- ``bulk_delete``: strictly more than BULK_DELETE_THRESHOLD (=3)
  PATIENT_DELETED rows from one user inside the review window.
- ``high_export_rate``: today's export count (EXPORT_ACTIONS =
  {patient_exported, export_action_taken}) must exceed the user's P95
  of daily export counts over the baseline AND clear an absolute floor
  of max(2, 2 * p95) — but only once the user has at least
  MIN_BASELINE_DAYS_FOR_EXPORT_RATE (=14) days of audit history. A user
  inside that warmup yields no alert: sparse history means "cannot
  judge", not "all clear".

The baseline distribution is computed from a wider audit slice
(window + baseline days), so these tests seed plain audit rows at day
offsets to shape each user's history.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.audit_review_service import BULK_DELETE_THRESHOLD

if TYPE_CHECKING:
    from .conftest import AuditReviewHarness


def _alerts(payload: dict, alert: str, user_id: str | None = None) -> list[dict]:
    return [
        a
        for a in payload["user_aggregates"]
        if a["alert"] == alert and (user_id is None or a["user_id"] == user_id)
    ]


def _seed_seasoned_exporter(
    h: AuditReviewHarness,
    *,
    exports_per_day: int,
    baseline_days: int = 20,
) -> tuple[str, str]:
    """User first seen ~30 days ago with ``exports_per_day`` exports on
    each of ``baseline_days`` distinct past days (days_ago 2..N+1, all
    safely outside the 24h review window). Returns (user_id, patient_id).
    """
    user = h.seed_user(name="Erin Exporter")
    patient = h.seed_patient(user, created_days_ago=60)
    h.audit(user, "patient_viewed", patient_id=patient, days_ago=30)
    for day in range(2, 2 + baseline_days):
        for _ in range(exports_per_day):
            h.audit(user, "patient_exported", patient_id=patient, days_ago=day)
    return user, patient


class TestBulkDelete:
    def test_four_deletes_in_window_fire_one_alert(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        user = h.seed_user(name="Dana Deleter")
        for _ in range(4):
            patient = h.seed_patient(user, created_days_ago=60)
            h.audit(user, "patient_deleted", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        (alert,) = _alerts(payload, "bulk_delete")
        assert alert["user_id"] == user
        assert alert["count"] == 4
        assert alert["threshold"] == BULK_DELETE_THRESHOLD
        assert alert["window_hours"] == 24

    def test_exactly_threshold_deletes_do_not_fire(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        user = h.seed_user(name="Dana Deleter")
        # Strict > threshold: exactly BULK_DELETE_THRESHOLD stays quiet.
        for _ in range(BULK_DELETE_THRESHOLD):
            patient = h.seed_patient(user, created_days_ago=60)
            h.audit(user, "patient_deleted", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        assert _alerts(payload, "bulk_delete") == []

    def test_two_bulk_deleters_yield_per_user_alerts(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        first = h.seed_user(name="Dana Deleter")
        second = h.seed_user(name="Pat Purger")
        for user in (first, second):
            for _ in range(4):
                patient = h.seed_patient(user, created_days_ago=60)
                h.audit(user, "patient_deleted", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        alerts = _alerts(payload, "bulk_delete")
        assert len(alerts) == 2
        assert {a["user_id"] for a in alerts} == {first, second}
        assert all(a["count"] == 4 for a in alerts)


class TestHighExportRate:
    def test_seasoned_exporter_burst_fires_with_baseline_fields(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        # ~1 export/day over 20 distinct past days => p95 of 1.0.
        user, patient = _seed_seasoned_exporter(h, exports_per_day=1)
        for _ in range(10):
            h.audit(user, "patient_exported", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        (alert,) = _alerts(payload, "high_export_rate", user_id=user)
        assert alert["count"] == 10
        assert alert["p95"] == 1.0
        assert alert["baseline_export_days"] == 20

    def test_today_within_baseline_norm_stays_quiet(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        # 2 exports/day baseline => p95 of 2.0; 2 today is not > p95.
        user, patient = _seed_seasoned_exporter(h, exports_per_day=2)
        for _ in range(2):
            h.audit(user, "patient_exported", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        assert _alerts(payload, "high_export_rate") == []

    def test_new_user_burst_is_suppressed_during_warmup(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        # First activity 10 days ago — inside the 14-day warmup. A burst
        # of 10 exports today yields nothing: the system cannot judge yet.
        user = h.seed_user(name="Nova Newcomer")
        patient = h.seed_patient(user, created_days_ago=10)
        h.audit(user, "patient_viewed", patient_id=patient, days_ago=10)
        for _ in range(10):
            h.audit(user, "patient_exported", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        assert _alerts(payload, "high_export_rate") == []

    def test_single_export_with_no_history_stays_under_floor(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        # Seasoned user, zero historical exports => p95 of 0. One export
        # today exceeds p95 but not the absolute floor of 2.
        user = h.seed_user(name="Quinn Quiet")
        patient = h.seed_patient(user, created_days_ago=60)
        h.audit(user, "patient_viewed", patient_id=patient, days_ago=30)
        h.audit(user, "patient_exported", patient_id=patient, hours_ago=1)

        payload = h.payload(window_hours=24)
        assert _alerts(payload, "high_export_rate") == []

    def test_both_export_actions_count_toward_todays_rate(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        user, patient = _seed_seasoned_exporter(h, exports_per_day=1)
        for _ in range(5):
            h.audit(user, "patient_exported", patient_id=patient, hours_ago=1)
        for _ in range(5):
            h.audit(user, "export_action_taken", patient_id=patient, hours_ago=2)

        payload = h.payload(window_hours=24)
        (alert,) = _alerts(payload, "high_export_rate", user_id=user)
        assert alert["count"] == 10
