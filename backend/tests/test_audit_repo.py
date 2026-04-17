# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for audit repository PHI-free invariant + historical-baseline flags."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.audit import AuditLogEntry
from app.repositories.audit import InMemoryAuditRepository, _assert_phi_free


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


class TestMetadataForReviewIsPhiFree:
    """metadata_for_review() output is sent to an LLM; must be PHI-free."""

    def test_clean_rows_pass_through(self) -> None:
        repo = InMemoryAuditRepository()
        repo.append(
            AuditLogEntry(
                user_id="u1",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p1",
                patient_id="p1",
                ip_address="10.0.0.1",
                changes={"changed_fields": ["first_name", "diagnosis"]},
            )
        )
        rows = repo.metadata_for_review(window_hours=24)
        assert len(rows) == 1
        assert rows[0]["user_id"] == "u1"
        assert rows[0]["changes"] == {"changed_fields": ["first_name", "diagnosis"]}

    def test_phi_top_level_key_is_rejected(self) -> None:
        payload = [{"user_id": "u", "user_email": "leak@example.com"}]
        with pytest.raises(AssertionError, match="user_email"):
            _assert_phi_free(payload)

    def test_phi_nested_key_is_rejected(self) -> None:
        payload = [
            {
                "user_id": "u",
                "changes": {"first_name": {"old": "A", "new": "B"}},
            }
        ]
        with pytest.raises(AssertionError, match="first_name"):
            _assert_phi_free(payload)

    def test_changed_fields_list_is_allowed(self) -> None:
        """The sanctioned shape — field names in a list, not as keys — is fine."""
        payload = [{"user_id": "u", "changes": {"changed_fields": ["first_name"]}}]
        _assert_phi_free(payload)  # does not raise

    def test_window_filters_by_timestamp(self) -> None:
        repo = InMemoryAuditRepository()
        old_ts = _iso(datetime.now(UTC) - timedelta(hours=48))
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="x",
                resource_type="patient",
                resource_id="1",
                timestamp=old_ts,
            )
        )
        repo.append(
            AuditLogEntry(
                user_id="u", action="y", resource_type="patient", resource_id="2"
            )
        )
        assert len(repo.metadata_for_review(window_hours=24)) == 1
        assert len(repo.metadata_for_review(window_hours=72)) == 2


class TestNoveltyFlags:
    """Each window row is enriched with flags comparing against a 90d baseline."""

    def test_novel_user_patient_flag(self) -> None:
        repo = InMemoryAuditRepository()
        # Baseline (30d ago): user-1 accessed patient-A
        repo.append(
            AuditLogEntry(
                user_id="user-1",
                action="patient_viewed",
                resource_type="patient",
                resource_id="patient-A",
                patient_id="patient-A",
                timestamp=_iso(datetime.now(UTC) - timedelta(days=30)),
            )
        )
        # Window (now): user-1 accesses patient-A (known) + patient-B (novel)
        repo.append(
            AuditLogEntry(
                user_id="user-1",
                action="patient_viewed",
                resource_type="patient",
                resource_id="patient-A",
                patient_id="patient-A",
            )
        )
        repo.append(
            AuditLogEntry(
                user_id="user-1",
                action="patient_viewed",
                resource_type="patient",
                resource_id="patient-B",
                patient_id="patient-B",
            )
        )
        rows = repo.metadata_for_review(window_hours=24, baseline_days=90)
        by_patient = {r["patient_id"]: r for r in rows}
        assert by_patient["patient-A"]["is_novel_user_patient"] is False
        assert by_patient["patient-B"]["is_novel_user_patient"] is True

    def test_novel_user_ip_flag(self) -> None:
        repo = InMemoryAuditRepository()
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p",
                patient_id="p",
                ip_address="10.0.0.1",
                timestamp=_iso(datetime.now(UTC) - timedelta(days=10)),
            )
        )
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p",
                patient_id="p",
                ip_address="203.0.113.7",  # new IP
            )
        )
        rows = repo.metadata_for_review(window_hours=24)
        assert len(rows) == 1
        assert rows[0]["is_novel_user_ip"] is True

    def test_brand_new_install_does_not_spam_novelty(self) -> None:
        """Day-one install with no baseline must NOT flag everything novel.

        This was the original bug: empty baseline → "(u, p) not in {}" → True
        for every row. A new therapist importing 20 patients on day one
        would get 20 false-positive 'novel access' findings.
        """
        repo = InMemoryAuditRepository()
        # Brand-new user accesses 20 patients today, no prior history at all
        for i in range(20):
            repo.append(
                AuditLogEntry(
                    user_id="new-user",
                    action="patient_viewed",
                    resource_type="patient",
                    resource_id=f"p{i}",
                    patient_id=f"p{i}",
                    ip_address="10.0.0.1",
                )
            )
        rows = repo.metadata_for_review(window_hours=24)
        assert len(rows) == 20
        # No baseline exists for this user — none of these should be novel
        assert not any(r["is_novel_user_patient"] for r in rows)
        assert not any(r["is_novel_user_ip"] for r in rows)

    def test_brand_new_user_alongside_established_users(self) -> None:
        """A new user joining a busy install must not get spammed."""
        repo = InMemoryAuditRepository()
        # Established user with baseline activity
        repo.append(
            AuditLogEntry(
                user_id="established",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p1",
                patient_id="p1",
                ip_address="10.0.0.1",
                timestamp=_iso(datetime.now(UTC) - timedelta(days=30)),
            )
        )
        # Brand-new user appears today
        repo.append(
            AuditLogEntry(
                user_id="newcomer",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p2",
                patient_id="p2",
                ip_address="10.0.0.2",
            )
        )
        rows = repo.metadata_for_review(window_hours=24)
        newcomer_row = next(r for r in rows if r["user_id"] == "newcomer")
        # No baseline for the newcomer — flags must not fire
        assert newcomer_row["is_novel_user_patient"] is False
        assert newcomer_row["is_novel_user_ip"] is False

    def test_patient_created_in_window_suppresses_novelty(self) -> None:
        """User creating a patient and immediately accessing it isn't suspicious."""
        repo = InMemoryAuditRepository()
        # Established user with baseline so novelty checks are even active
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="old",
                patient_id="old",
                timestamp=_iso(datetime.now(UTC) - timedelta(days=10)),
            )
        )
        # Today: user creates a new patient and views them
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_created",
                resource_type="patient",
                resource_id="new",
                patient_id="new",
            )
        )
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="new",
                patient_id="new",
            )
        )
        rows = repo.metadata_for_review(window_hours=24)
        new_patient_rows = [r for r in rows if r["patient_id"] == "new"]
        assert len(new_patient_rows) == 2
        # Neither the create nor the immediate view should be flagged novel
        assert not any(r["is_novel_user_patient"] for r in new_patient_rows)

    def test_baseline_excludes_window_itself(self) -> None:
        """A pair seen only *within* the review window stays unflagged when
        the user has no prior baseline (warmup case)."""
        repo = InMemoryAuditRepository()
        # Two rows in the same 24h window, same (user, patient) — no baseline
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p",
                patient_id="p",
                ip_address="10.0.0.1",
            )
        )
        repo.append(
            AuditLogEntry(
                user_id="u",
                action="patient_viewed",
                resource_type="patient",
                resource_id="p",
                patient_id="p",
                ip_address="10.0.0.1",
            )
        )
        rows = repo.metadata_for_review(window_hours=24)
        # No baseline for this user → no novelty claims (warmup behavior)
        assert not any(r["is_novel_user_patient"] for r in rows)
