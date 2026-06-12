# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``is_novel_user_patient`` against the real Postgres novelty SQL.

The flag definition (``AuditRepository.metadata_for_review``): true when
the user has >= 7 days of prior audit history AND has not touched this
patient in the preceding 90 days AND did not create the patient in the
review window. The warmup/baseline arithmetic lives in SQL
(``GROUP BY user_id HAVING min(timestamp) < cutoff``), which the
in-memory unit tests cannot exercise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .conftest import AuditReviewHarness


def _rows_for(payload: dict, user_id: str, patient_id: str) -> list[dict]:
    return [
        e for e in payload["entries"] if e["user_id"] == user_id and e["patient_id"] == patient_id
    ]


class TestNovelUserPatient:
    def test_seasoned_user_first_touch_is_novel(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        snooper = h.seed_user(name="Sam Snooper")
        patient = h.seed_patient(owner, created_days_ago=60)
        # Snooper has 30 days of unrelated history (past warmup) …
        h.audit(snooper, "patient_viewed", days_ago=30)
        # … and touches this patient for the first time today.
        h.audit(snooper, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, snooper, patient)
        assert row["is_novel_user_patient"] is True

    def test_recently_seen_pair_is_not_novel(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        # Owner saw the patient 30 days ago — pair is in the baseline.
        h.audit(owner, "patient_viewed", patient_id=patient, days_ago=30)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient)
        assert row["is_novel_user_patient"] is False


class TestNoveltyUserHistoryMatrix:
    """Boundary matrix for the user-history warmup and the pair baseline.

    Pins the two constants that define the flag: ``MIN_USER_BASELINE_DAYS``
    (7 — earliest audit row must predate it before the user is judged at
    all) and ``baseline_days`` (90 — how far back a (user, patient) pair
    stays "known"). Warmup yields False, which means "cannot judge", not
    "all clear".
    """

    def test_brand_new_user_first_row_today_is_warmup(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        newbie = h.seed_user(name="Noa Newhire")
        patient = h.seed_patient(owner, created_days_ago=60)
        # The newbie's first-ever audit row is today's view: there is no
        # history to compare against, so the flag must stay quiet.
        h.audit(newbie, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, newbie, patient)
        assert row["is_novel_user_patient"] is False

    def test_user_history_just_inside_warmup_is_not_judged(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        viewer = h.seed_user(name="Robin Recent")
        patient = h.seed_patient(owner, created_days_ago=60)
        # Earliest activity 6.9 days ago — just inside the 7-day warmup,
        # so the user is not yet judged even on a true first touch.
        h.audit(viewer, "patient_viewed", days_ago=6.9)
        h.audit(viewer, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, viewer, patient)
        assert row["is_novel_user_patient"] is False

    def test_user_history_just_past_warmup_first_touch_is_novel(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        viewer = h.seed_user(name="Robin Seasoned")
        patient = h.seed_patient(owner, created_days_ago=60)
        # Earliest activity 7.1 days ago — just past the 7-day warmup;
        # a first-ever touch of this patient now counts as a novel pair.
        h.audit(viewer, "patient_viewed", days_ago=7.1)
        h.audit(viewer, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, viewer, patient)
        assert row["is_novel_user_patient"] is True

    def test_pair_seen_thirty_days_ago_is_inside_baseline(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=60)
        h.audit(owner, "patient_viewed", patient_id=patient, days_ago=30)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient)
        assert row["is_novel_user_patient"] is False

    def test_pair_seen_near_baseline_edge_is_still_known(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        patient = h.seed_patient(owner, created_days_ago=120)
        # 89.5 days ago — old, but still inside the 90-day baseline, so
        # the pair is known and today's view is not novel.
        h.audit(owner, "patient_viewed", patient_id=patient, days_ago=89.5)
        h.audit(owner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, owner, patient)
        assert row["is_novel_user_patient"] is False

    def test_dormant_patient_outside_baseline_is_novel(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # The workhorse insider-snooping signal: a continuously active
        # user pulls up a chart they have not touched in over 90 days.
        # The old contact aged out of the baseline, so the pair reads as
        # new — exactly the "why is this dormant chart suddenly open?"
        # case a reviewer wants surfaced.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        viewer = h.seed_user(name="Casey Curious")
        patient = h.seed_patient(owner, created_days_ago=120)
        # Pair last seen 95 days ago — just outside the 90-day baseline.
        h.audit(viewer, "patient_viewed", patient_id=patient, days_ago=95)
        # Viewer is otherwise continuously active on unrelated work.
        for days in (80.0, 60.0, 40.0, 20.0, 5.0):
            h.audit(viewer, "patient_viewed", days_ago=days)
        h.audit(viewer, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, viewer, patient)
        assert row["is_novel_user_patient"] is True

    def test_two_week_vacation_does_not_create_false_positive(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        clinician = h.seed_user(name="Vera Vacationer")
        patient = h.seed_patient(clinician, created_days_ago=60)
        # Active stretch from ~89 to ~16 days ago, including a view of
        # this patient 20 days ago …
        for days in (89.0, 60.0, 40.0, 16.0):
            h.audit(clinician, "patient_viewed", days_ago=days)
        h.audit(clinician, "patient_viewed", patient_id=patient, days_ago=20)
        # … then a two-week gap (vacation) and a return today. The pair
        # is still inside the 90-day baseline: no flag.
        h.audit(clinician, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        rows = _rows_for(payload, clinician, patient)
        today_row = max(rows, key=lambda r: r["timestamp"])
        assert today_row["is_novel_user_patient"] is False

    def test_return_after_long_absence_flags_aged_out_pair(
        self, audit_review: AuditReviewHarness
    ) -> None:
        # A user who has been away for longer than the baseline (95+
        # days) comes back: every pair they ever knew has aged out, so
        # their first view reads as novel even though they treated the
        # patient before. Noisy-but-conservative by design — a return
        # from a months-long absence straight into a chart is itself
        # worth a reviewer's glance, so we accept the flag rather than
        # special-case it away.
        h = audit_review
        owner = h.seed_user(name="Dana Owner")
        returner = h.seed_user(name="Lou Longgone")
        patient = h.seed_patient(owner, created_days_ago=130)
        # Earliest activity ~200 days ago; pair only ever seen ~120 days
        # ago; nothing at all in the last 95 days.
        h.audit(returner, "patient_viewed", days_ago=200)
        h.audit(returner, "patient_viewed", days_ago=150)
        h.audit(returner, "patient_viewed", patient_id=patient, days_ago=120)
        h.audit(returner, "patient_viewed", days_ago=100)
        h.audit(returner, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, returner, patient)
        assert row["is_novel_user_patient"] is True

    def test_same_window_create_suppresses_novelty(self, audit_review: AuditReviewHarness) -> None:
        h = audit_review
        creator = h.seed_user(name="Pat Producer")
        # Seasoned user (past warmup) creates a patient inside the review
        # window and immediately works on them: the create suppresses the
        # novelty flag for the creator.
        h.audit(creator, "patient_viewed", days_ago=30)
        patient = h.seed_patient(creator, created_days_ago=0)
        h.audit(creator, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        rows = _rows_for(payload, creator, patient)
        assert rows  # the create + the view both land in the window
        assert all(r["is_novel_user_patient"] is False for r in rows)

    def test_other_user_viewing_just_created_patient_is_novel(
        self, audit_review: AuditReviewHarness
    ) -> None:
        h = audit_review
        creator = h.seed_user(name="Pat Producer")
        bystander = h.seed_user(name="Blake Bystander")
        h.audit(creator, "patient_viewed", days_ago=30)
        h.audit(bystander, "patient_viewed", days_ago=30)
        patient = h.seed_patient(creator, created_days_ago=0)
        # The create only suppresses novelty for the creating user; a
        # different seasoned user touching the brand-new patient is
        # still a novel pair.
        h.audit(bystander, "patient_viewed", patient_id=patient)

        payload = h.payload(window_hours=24)
        (row,) = _rows_for(payload, bystander, patient)
        assert row["is_novel_user_patient"] is True
