# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for trial session limit enforcement."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.routes.subscription import (
    TrialLimitReachedError,
    check_and_count_trial_session,
)


def _mock_settings(*, is_saas: bool = True, db_backend: str = "postgres") -> MagicMock:
    settings = MagicMock()
    settings.is_saas = is_saas
    settings.database_backend = db_backend
    return settings


def _mock_doc(exists: bool, data: dict[str, Any] | None = None) -> MagicMock:
    doc = MagicMock()
    doc.exists = exists
    doc.to_dict.return_value = data or {}
    return doc


class TestCheckAndCountTrialSession:
    """Unit tests for check_and_count_trial_session."""

    def test_noop_for_non_saas(self) -> None:
        """Self-hosted installations have no trial limits."""
        settings = _mock_settings(is_saas=False)
        # Should not raise
        check_and_count_trial_session("dr@example.com", settings)

    @patch("app.routes.subscription._fetch_subscription")
    def test_noop_for_paid_active(self, mock_fetch: MagicMock) -> None:
        """Paid active subscribers have no session limits."""
        mock_fetch.return_value = {
            "status": "active",
            "effective_status": "active",
            "trial_sessions_used": 0,
            "trial_sessions_limit": 15,
        }
        settings = _mock_settings()
        check_and_count_trial_session("dr@example.com", settings)

    @patch("app.routes.subscription._increment_trial_sessions")
    @patch("app.routes.subscription._fetch_subscription")
    def test_trial_under_limit_allowed(
        self, mock_fetch: MagicMock, mock_increment: MagicMock
    ) -> None:
        """Trial user with sessions remaining can create sessions."""
        mock_fetch.return_value = {
            "status": "trial",
            "effective_status": "trial",
            "trial_sessions_used": 5,
            "trial_sessions_limit": 15,
        }
        mock_increment.return_value = 6
        settings = _mock_settings()

        check_and_count_trial_session("dr@example.com", settings)

        mock_increment.assert_called_once_with("dr@example.com", settings)

    @patch("app.routes.subscription._expire_trial")
    @patch("app.routes.subscription._fetch_subscription")
    def test_trial_at_limit_blocks(self, mock_fetch: MagicMock, mock_expire: MagicMock) -> None:
        """Trial user who has hit the limit gets blocked."""
        mock_fetch.return_value = {
            "status": "trial",
            "effective_status": "trial",
            "trial_sessions_used": 15,
            "trial_sessions_limit": 15,
        }
        settings = _mock_settings()

        with pytest.raises(TrialLimitReachedError) as exc_info:
            check_and_count_trial_session("dr@example.com", settings)

        assert exc_info.value.used == 15
        assert exc_info.value.limit == 15
        mock_expire.assert_called_once_with("dr@example.com", settings)

    @patch("app.routes.subscription._expire_trial")
    @patch("app.routes.subscription._increment_trial_sessions")
    @patch("app.routes.subscription._fetch_subscription")
    def test_trial_increment_triggers_expiration(
        self,
        mock_fetch: MagicMock,
        mock_increment: MagicMock,
        mock_expire: MagicMock,
    ) -> None:
        """Using the last trial session expires the trial."""
        mock_fetch.return_value = {
            "status": "trial",
            "effective_status": "trial",
            "trial_sessions_used": 14,
            "trial_sessions_limit": 15,
        }
        mock_increment.return_value = 15  # Now at limit
        settings = _mock_settings()

        # Should NOT raise — the session is allowed
        check_and_count_trial_session("dr@example.com", settings)

        # But trial should be expired for next time
        mock_expire.assert_called_once_with("dr@example.com", settings)

    @patch("app.routes.subscription._fetch_subscription")
    def test_grace_extension_bypasses_limit(self, mock_fetch: MagicMock) -> None:
        """Grace-extended users pass through — care comes first."""
        mock_fetch.return_value = {
            "status": "trial_expired",
            "effective_status": "active",  # Grace makes it active
            "trial_sessions_used": 15,
            "trial_sessions_limit": 15,
            "grace_extension_available": False,
            "grace_extension_expires_at": "2099-01-01T00:00:00Z",
        }
        settings = _mock_settings()

        # Should not raise — grace extension active
        check_and_count_trial_session("dr@example.com", settings)

    @patch("app.routes.subscription._fetch_subscription")
    def test_no_subscription_record_allowed(self, mock_fetch: MagicMock) -> None:
        """Mid-provisioning users without subscription pass through."""
        mock_fetch.return_value = None
        settings = _mock_settings()
        check_and_count_trial_session("dr@example.com", settings)


class TestTrialEnforcementIntegration:
    """Integration tests for trial enforcement in session routes."""

    @patch("app.routes.sessions.check_and_count_trial_session")
    def test_schedule_session_blocked_at_limit(
        self,
        mock_check: MagicMock,
        client: Any,
        mock_repo: Any,
        mock_user: Any,
    ) -> None:
        """Schedule endpoint returns 402 when trial is exhausted."""
        mock_check.side_effect = TrialLimitReachedError(15, 15)

        # Create a patient first
        resp = client.post(
            "/api/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
            },
        )
        assert resp.status_code == 201
        patient_id = resp.json()["id"]

        resp = client.post(
            "/api/sessions/schedule",
            json={
                "patient_id": patient_id,
                "scheduled_at": "2026-04-15T10:00:00Z",
            },
        )

        assert resp.status_code == 402
        body = resp.json()
        assert body["detail"]["error"]["code"] == "TRIAL_LIMIT_REACHED"
        assert body["detail"]["error"]["details"]["sessions_used"] == 15

    @patch("app.routes.sessions.check_and_count_trial_session")
    def test_schedule_session_allowed_during_trial(
        self,
        mock_check: MagicMock,
        client: Any,
        mock_repo: Any,
        mock_user: Any,
    ) -> None:
        """Schedule succeeds when trial check passes."""
        mock_check.return_value = None  # No error

        resp = client.post(
            "/api/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
            },
        )
        assert resp.status_code == 201
        patient_id = resp.json()["id"]

        resp = client.post(
            "/api/sessions/schedule",
            json={
                "patient_id": patient_id,
                "scheduled_at": "2026-04-15T10:00:00Z",
            },
        )

        assert resp.status_code == 201
        mock_check.assert_called_once()

    @patch("app.routes.sessions.check_and_count_trial_session")
    def test_upload_session_blocked_at_limit(
        self,
        mock_check: MagicMock,
        client: Any,
        mock_repo: Any,
        mock_user: Any,
    ) -> None:
        """Upload endpoint returns 402 when trial is exhausted."""
        mock_check.side_effect = TrialLimitReachedError(15, 15)

        resp = client.post(
            "/api/patients",
            json={
                "first_name": "Jane",
                "last_name": "Doe",
                "email": "jane@example.com",
            },
        )
        assert resp.status_code == 201
        patient_id = resp.json()["id"]

        resp = client.post(
            f"/api/patients/{patient_id}/sessions/upload",
            json={
                "patient_id": patient_id,
                "session_date": "2026-04-15T10:00:00Z",
                "transcript": {
                    "format": "txt",
                    "content": "Therapist: Hello.\nClient: Hi.",
                },
            },
        )

        assert resp.status_code == 402
        body = resp.json()
        assert body["detail"]["error"]["code"] == "TRIAL_LIMIT_REACHED"
