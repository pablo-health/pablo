# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the user profile PATCH endpoint and provider_type field."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from app.models import User
from app.repositories import InMemoryIdentityRepository, InMemoryUserRepository


class TestUpdateProfile:
    """Test PATCH /api/users/me."""

    def test_set_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"provider_type": "prescriber"})

        assert response.status_code == 200
        assert response.json()["provider_type"] == "prescriber"
        assert mock_user_repo.get(mock_user.id).provider_type == "prescriber"  # type: ignore[union-attr]

    def test_provider_type_accepts_all_three_values(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        for value in ("therapist", "prescriber", "both"):
            response = client.patch("/api/users/me", json={"provider_type": value})
            assert response.status_code == 200
            assert response.json()["provider_type"] == value

    def test_invalid_provider_type_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"provider_type": "physician"})
        assert response.status_code == 422

    def test_partial_update_preserves_unspecified_fields(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.provider_type = "therapist"
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"name": "Dr. Renamed"})

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Dr. Renamed"
        assert body["provider_type"] == "therapist"

    def test_user_status_includes_provider_type(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes provider_type so the frontend
        can gate the onboarding redirect on it."""
        mock_user.provider_type = "both"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] == "both"

    def test_user_status_provider_type_null_for_new_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A user who has never picked a provider_type returns null,
        which is the onboarding-needed signal."""
        mock_user.provider_type = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["provider_type"] is None


class TestOnboardingState:
    """Test the onboarding_state field on PATCH /api/users/me and
    GET /api/users/me/status."""

    def test_set_onboarding_state_completed(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.patch("/api/users/me", json={"onboarding_state": "completed"})

        assert response.status_code == 200
        assert response.json()["onboarding_state"] == "completed"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.onboarding_state == "completed"

    def test_onboarding_state_accepts_all_three_values(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        for value in ("in_progress", "later", "completed"):
            response = client.patch("/api/users/me", json={"onboarding_state": value})
            assert response.status_code == 200
            assert response.json()["onboarding_state"] == value

    def test_invalid_onboarding_state_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)
        response = client.patch("/api/users/me", json={"onboarding_state": "skipped"})
        assert response.status_code == 422

    def test_user_status_includes_onboarding_state(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes onboarding_state so the
        SaaS overlay can decide whether to show the resume banner."""
        mock_user.onboarding_state = "later"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["onboarding_state"] == "later"

    def test_user_status_onboarding_state_null_for_grandfathered_users(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """A row from before this column existed returns null, which
        the SaaS overlay treats as 'already completed' (no banner,
        no redirect)."""
        mock_user.onboarding_state = None
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["onboarding_state"] is None


class TestSecurityGuideAcknowledgment:
    """Test POST /api/users/me/acknowledge-security-guide and the
    related GET endpoint + /me/status exposure."""

    def test_acknowledge_records_timestamp_and_version(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update(mock_user)

        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-05-14"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is True
        assert body["version"] == "2026-05-14"
        assert body["acknowledged_at"] is not None

        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.security_guide_version == "2026-05-14"
        assert stored.security_guide_acknowledged_at is not None

    def test_invalid_version_format_rejected(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """Versions must match YYYY-MM-DD, mirroring the BAA pattern."""
        mock_user_repo.update(mock_user)
        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "v2.0"},
        )
        assert response.status_code == 422

    def test_acknowledge_is_idempotent_and_overwrites(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """Re-acknowledging (e.g. after a version bump) overwrites
        both fields. The frontend prompts on version mismatch."""
        mock_user_repo.update(mock_user)
        client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-02-16"},
        )
        response = client.post(
            "/api/users/me/acknowledge-security-guide",
            json={"version": "2026-05-14"},
        )
        assert response.status_code == 200
        assert response.json()["version"] == "2026-05-14"

    def test_status_endpoint_reflects_acknowledgment(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.security_guide_acknowledged_at = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
        mock_user.security_guide_version = "2026-05-14"
        mock_user_repo.update(mock_user)

        response = client.get("/api/users/me/security-guide-status")

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is True
        assert body["version"] == "2026-05-14"

    def test_status_endpoint_reports_not_acknowledged(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user.security_guide_acknowledged_at = None
        mock_user.security_guide_version = None
        mock_user_repo.update(mock_user)

        response = client.get("/api/users/me/security-guide-status")

        assert response.status_code == 200
        body = response.json()
        assert body["acknowledged"] is False
        assert body["version"] is None
        assert body["acknowledged_at"] is None

    def test_user_status_includes_security_guide_fields(
        self, client: Any, mock_user: User, mock_user_repo: InMemoryUserRepository
    ) -> None:
        """GET /api/users/me/status exposes the guide fields so the
        SaaS overlay can decide whether to redirect to the guide step."""
        mock_user.security_guide_acknowledged_at = datetime(2026, 5, 14, 12, 0, tzinfo=UTC)
        mock_user.security_guide_version = "2026-05-14"
        mock_user_repo.update(mock_user)

        with patch("app.settings.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        body = response.json()
        assert body["security_guide_version"] == "2026-05-14"
        assert body["security_guide_acknowledged_at"] is not None


class TestRecordMfaEnrollment:
    """Test POST /api/users/me/mfa-enrolled.

    Regression guard for THERAPY-glzf-2: the handler must look up the
    Firebase uid via the identity repository's reverse-lookup, not by
    assuming ``user.id`` is the Firebase uid. Post-indirection, those
    two values diverge for any self-serve signup.
    """

    def _fake_totp_user(self) -> SimpleNamespace:
        return SimpleNamespace(
            multi_factor=SimpleNamespace(enrolled_factors=[SimpleNamespace(factor_id="totp")])
        )

    def _fake_non_totp_user(self) -> SimpleNamespace:
        return SimpleNamespace(multi_factor=SimpleNamespace(enrolled_factors=[]))

    def test_resolves_firebase_uid_via_identity_repo(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_identity_repo: InMemoryIdentityRepository,
        mock_user_id: str,
    ) -> None:
        """Post-indirection: user.id is a Pablo uuid, Firebase uid is separate.

        The handler must call firebase_auth.get_user with the Firebase uid
        looked up from the identity table, not with user.id. The default
        fixture pre-links (firebase, mock_user_id) -> mock_user_id as a
        legacy-backfill record; here we re-link to model a fresh signup
        where the two diverge.
        """
        # Re-link to model the post-indirection case
        mock_identity_repo._mappings.clear()
        mock_identity_repo.link("firebase", "firebase-uid-distinct", mock_user_id)
        mock_user_repo.update(mock_user)

        captured: dict[str, str] = {}

        def fake_get_user(uid: str) -> Any:
            captured["uid"] = uid
            return self._fake_totp_user()

        with patch("app.routes.users.firebase_auth.get_user", side_effect=fake_get_user):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 200
        assert "mfa_enrolled_at" in response.json()
        assert captured["uid"] == "firebase-uid-distinct", (
            "handler must use the identity-repo reverse-lookup, not user.id"
        )
        stored = mock_user_repo.get(mock_user_id)
        assert stored is not None
        assert stored.mfa_enrolled_at is not None

    def test_missing_identity_mapping_is_500(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
        mock_identity_repo: InMemoryIdentityRepository,
    ) -> None:
        """An authenticated user with no firebase identity row is a server
        invariant violation, not a 404. Surface it as 500 so the alert
        fires instead of treating it as a routine client error."""
        mock_identity_repo._mappings.clear()
        mock_user_repo.update(mock_user)

        fb_get_user = MagicMock()
        with patch("app.routes.users.firebase_auth.get_user", fb_get_user):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "IDENTITY_MAPPING_MISSING"
        fb_get_user.assert_not_called()
        stored = mock_user_repo.get(mock_user.id)
        # No timestamp write on the failure path
        assert stored is not None
        assert stored.mfa_enrolled_at is None

    def test_no_totp_factor_rejected(
        self,
        client: Any,
        mock_user: User,
        mock_user_repo: InMemoryUserRepository,
    ) -> None:
        mock_user_repo.update(mock_user)

        with patch(
            "app.routes.users.firebase_auth.get_user",
            return_value=self._fake_non_totp_user(),
        ):
            response = client.post("/api/users/me/mfa-enrolled")

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "MFA_NOT_ENROLLED"
        stored = mock_user_repo.get(mock_user.id)
        assert stored is not None
        assert stored.mfa_enrolled_at is None
