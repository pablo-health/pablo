# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``is_same_last_name`` and ``is_no_treatment_relationship`` against Postgres.

The surname flag depends on grant-gated patient lookups (the service can
only resolve a patient's last name through a user that holds a
``patient_clinicians`` grant), and the care-team flag depends on the
grant-gated appointment/session reads plus the intake-suppression and
warmup thresholds in ``audit_review_service``. None of that gating exists
in the in-memory unit doubles, so these definitions are pinned here.

Thresholds under test (see ``app.services.audit_review_service``):

  - PATIENT_INTAKE_SUPPRESSION_DAYS = 14
  - MIN_APPOINTMENTS_FOR_CARETEAM_CHECK = 5
  - APPOINTMENT_PROXIMITY_DAYS = 7 (session window is +/- 1 day)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.services.audit_review_service import MIN_APPOINTMENTS_FOR_CARETEAM_CHECK
from sqlalchemy import text

if TYPE_CHECKING:
    from .conftest import AuditReviewHarness

# The appointment repository's access gate calls the has_patient_access()
# SQL function, which is installed by migrations — not by the ORM
# metadata the shared harness materializes. Recreate it here (same body
# as the migration, unqualified so it lands in the practice schema via
# the harness session's search_path); CREATE OR REPLACE is idempotent.
_HAS_PATIENT_ACCESS_FN = """
CREATE OR REPLACE FUNCTION has_patient_access(
    p_patient_id UUID,
    p_user_id    VARCHAR
) RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1 FROM patient_clinicians
        WHERE patient_id = p_patient_id
          AND user_id::text = p_user_id
          AND (expires_at IS NULL OR expires_at > now())
    );
$$
"""


@pytest.fixture(autouse=True)
def _install_has_patient_access(audit_review: AuditReviewHarness) -> None:
    audit_review.session.execute(text(_HAS_PATIENT_ACCESS_FN))
    audit_review.session.flush()


def _rows_for(
    payload: dict, user_id: str, patient_id: str, action: str | None = None
) -> list[dict]:
    return [
        e
        for e in payload["entries"]
        if e["user_id"] == user_id
        and e["patient_id"] == patient_id
        and (action is None or e["action"] == action)
    ]


def _seed_warmup_appointments(h: AuditReviewHarness, user_id: str, count: int) -> None:
    """Give ``user_id`` ``count`` past appointments with an unrelated
    patient of their own, so the care-team check clears (or doesn't clear)
    system warmup without touching the patient under test."""
    roster_patient = h.seed_patient(
        user_id, last_name="Rosterson", created_days_ago=200, log_create=False
    )
    for i in range(count):
        h.seed_appointment(user_id, roster_patient, start_days_from_now=-(30 + i))


class TestSameLastName:
    def test_matching_surname_flags(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Casey Garcia")
        patient = h.seed_patient(owner, last_name="Garcia")
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_same_last_name"] is True

    def test_different_surname_does_not_flag(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Casey Garcia")
        patient = h.seed_patient(owner, last_name="Nakamura")
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_same_last_name"] is False

    def test_match_is_case_insensitive(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Casey GARCIA")
        patient = h.seed_patient(owner, last_name="garcia")
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_same_last_name"] is True

    def test_single_token_name_is_treated_as_surname(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # _extract_surname takes the last whitespace token; a one-token
        # name IS that token.
        h = audit_review
        owner = h.seed_user(name="Garcia")
        patient = h.seed_patient(owner, last_name="Garcia")
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_same_last_name"] is True


class TestNoTreatmentRelationship:
    def test_view_without_appointment_or_session_flags(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # Exactly MIN_APPOINTMENTS_FOR_CARETEAM_CHECK pins the warmup
        # boundary: 5 appointments is enough to judge.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is True

    def test_appointment_inside_proximity_window_supports_access(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.seed_appointment(owner, patient, start_days_from_now=3)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is False

    def test_appointment_outside_proximity_window_does_not_support_access(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # 10 days out is past APPOINTMENT_PROXIMITY_DAYS=7 — the
        # appointment exists but doesn't explain today's view.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.seed_appointment(owner, patient, start_days_from_now=10)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is True

    def test_same_day_finalized_session_supports_access(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.seed_therapy_session(owner, patient, days_ago=0, status="finalized")
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is False

    def test_intake_window_suppresses_flag(self, audit_review: AuditReviewHarness) -> None:
        # Patient created 5 days ago — inside the 14-day intake window.
        # New-patient intake naturally has no appointments yet, so the
        # flag stays off even though nothing supports the access.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=5)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is False

    def test_user_below_appointment_warmup_cannot_be_judged(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # 4 appointments (one below the threshold) means "cannot judge",
        # not "all clear" — same unsupported view as the flagging case.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK - 1)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert row["is_no_treatment_relationship"] is False

    def test_ungranted_viewer_flags_even_when_owner_has_appointments(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # The appointment lookup is grant-gated: the snooper holds no
        # patient_clinicians grant, so the owner's same-day appointment
        # is invisible from the snooper's vantage point. That is exactly
        # the signal — an access with no care-team relationship behind it.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        snooper = h.seed_user(name="Sam Snooper")
        patient = h.seed_patient(owner, created_days_ago=60)
        h.seed_appointment(owner, patient, start_days_from_now=0)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK - 1)
        _seed_warmup_appointments(h, snooper, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.audit(owner, "patient_viewed", patient_id=patient)
        h.audit(snooper, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (snooper_row,) = _rows_for(payload, snooper, patient, "patient_viewed")
        assert snooper_row["is_no_treatment_relationship"] is True
        # Contrast: the owner's identical view is supported — the same-day
        # appointment IS visible through the owner's grant. (The owner's
        # patient appointment plus the 4 roster ones also clear warmup.)
        (owner_row,) = _rows_for(payload, owner, patient, "patient_viewed")
        assert owner_row["is_no_treatment_relationship"] is False

    def test_non_view_actions_never_flag(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        _seed_warmup_appointments(h, owner, MIN_APPOINTMENTS_FOR_CARETEAM_CHECK)
        h.audit(owner, "patient_updated", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient, "patient_updated")
        assert row["is_no_treatment_relationship"] is False
