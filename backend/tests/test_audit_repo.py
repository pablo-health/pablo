# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for audit repository PHI-free invariant."""

from datetime import UTC, datetime, timedelta

import pytest
from app.models.audit import AuditLogEntry
from app.repositories.audit import InMemoryAuditRepository, _assert_phi_free


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
        old_ts = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")
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
