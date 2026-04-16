# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Firebase authentication and Identity Platform multi-tenancy."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.service import (
    TenantContext,
    _get_cached_token,
    get_current_user,
    get_current_user_id,
    get_tenant_context,
    require_mfa,
    verify_firebase_token,
)
from app.db import _request_session
from app.models import User
from app.repositories import InMemoryAllowlistRepository, InMemoryUserRepository
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth

VERIFY_PATCH = "app.auth.service.firebase_auth.verify_id_token"


class TestVerifyFirebaseToken:
    """Test Firebase token verification."""

    def test_valid_token(self, mock_firebase_init: Any) -> None:
        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.return_value = {"uid": "user123", "email": "test@example.com"}

            result = verify_firebase_token("valid-token")

            assert result["uid"] == "user123"
            mock_verify.assert_called_once_with("valid-token", check_revoked=True)
            mock_firebase_init.assert_called_once()

    def test_expired_token(self) -> None:
        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.side_effect = firebase_auth.ExpiredIdTokenError("Token expired", cause=None)

            with pytest.raises(HTTPException) as exc_info:
                verify_firebase_token("expired-token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail["error"]["code"] == "TOKEN_EXPIRED"  # type: ignore[index]

    def test_invalid_token(self) -> None:
        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.side_effect = firebase_auth.InvalidIdTokenError("Bad token")

            with pytest.raises(HTTPException) as exc_info:
                verify_firebase_token("invalid-token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]

    def test_revoked_token(self) -> None:
        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.side_effect = firebase_auth.RevokedIdTokenError("Token revoked")

            with pytest.raises(HTTPException) as exc_info:
                verify_firebase_token("revoked-token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail["error"]["code"] == "TOKEN_REVOKED"  # type: ignore[index]

    def test_disabled_user(self) -> None:
        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.side_effect = firebase_auth.UserDisabledError("User disabled")

            with pytest.raises(HTTPException) as exc_info:
                verify_firebase_token("disabled-user-token")

            assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc_info.value.detail["error"]["code"] == "USER_DISABLED"  # type: ignore[index]


class TestTokenCaching:
    """Test middleware token caching to avoid double verification."""

    def test_returns_cached_token_when_raw_matches(self) -> None:
        request = MagicMock()
        request.state.verified_firebase_token_raw = "the-jwt"
        request.state.decoded_firebase_token = {"uid": "cached-user"}

        result = _get_cached_token(request, "the-jwt")
        assert result == {"uid": "cached-user"}

    def test_returns_none_when_raw_does_not_match(self) -> None:
        request = MagicMock()
        request.state.verified_firebase_token_raw = "old-jwt"
        request.state.decoded_firebase_token = {"uid": "cached-user"}

        result = _get_cached_token(request, "different-jwt")
        assert result is None

    def test_returns_none_when_no_cache(self) -> None:
        request = MagicMock()
        request.state = MagicMock(spec=[])  # state exists but has no cache attrs
        result = _get_cached_token(request, "any-jwt")
        assert result is None

    def test_returns_none_when_request_is_none(self) -> None:
        result = _get_cached_token(None, "any-jwt")
        assert result is None

    @patch("app.auth.service.verify_firebase_token")
    def test_require_mfa_skips_verification_with_cache(self, mock_verify: MagicMock) -> None:
        """require_mfa uses cached token instead of re-verifying."""
        mock_request = MagicMock()
        mock_request.state.verified_firebase_token_raw = "cached-token"
        mock_request.state.decoded_firebase_token = {
            "uid": "user123",
            "firebase": {"sign_in_second_factor": "phone"},
        }
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "cached-token"

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = True
            result = require_mfa(mock_request, mock_credentials)

        assert result["uid"] == "user123"
        mock_verify.assert_not_called()

    @patch("app.auth.service.verify_firebase_token")
    def test_require_mfa_falls_back_without_cache(self, mock_verify: MagicMock) -> None:
        """require_mfa calls verify_firebase_token when no cache present."""
        mock_request = MagicMock()
        mock_request.state = MagicMock(spec=[])  # state exists but no cache
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "uncached-token"
        mock_verify.return_value = {
            "uid": "user123",
            "firebase": {"sign_in_second_factor": "phone"},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = True
            result = require_mfa(mock_request, mock_credentials)

        assert result["uid"] == "user123"
        mock_verify.assert_called_once_with("uncached-token")


class TestGetCurrentUserId:
    """Test user ID extraction from Firebase token."""

    @patch("app.auth.service.verify_firebase_token")
    def test_extract_user_id(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "valid-token"
        mock_verify.return_value = {"uid": "user123", "email": "test@example.com"}

        user_id = get_current_user_id(MagicMock(), mock_credentials)

        assert user_id == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_missing_uid(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token-without-uid"
        mock_verify.return_value = {"email": "test@example.com"}

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_id(MagicMock(), mock_credentials)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]
        assert "User ID not found" in exc_info.value.detail["error"]["message"]  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_propagates_auth_error(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "bad-token"
        mock_verify.side_effect = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "INVALID_TOKEN", "message": "Bad", "details": {}}},
        )

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_id(MagicMock(), mock_credentials)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


class TestRequireMfa:
    """Test MFA enforcement."""

    @patch("app.auth.service.verify_firebase_token")
    def test_passes_with_mfa(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "mfa-token"
        mock_verify.return_value = {
            "uid": "user123",
            "firebase": {"sign_in_second_factor": "phone"},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            result = require_mfa(MagicMock(), mock_credentials)

        assert result["uid"] == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_fails_without_mfa_in_production(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "no-mfa-token"
        mock_verify.return_value = {"uid": "user123", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = True
            with pytest.raises(HTTPException) as exc_info:
                require_mfa(MagicMock(), mock_credentials)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "MFA_REQUIRED"  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_skipped_in_development(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "dev-token"
        mock_verify.return_value = {"uid": "user123", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = True
            mock_settings.return_value.require_mfa = True
            result = require_mfa(MagicMock(), mock_credentials)

        assert result["uid"] == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_email_bypasses_mfa_in_non_production(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "e2e-token"
        mock_verify.return_value = {
            "uid": "e2e-user",
            "email": "test@pablo.health",
            "email_verified": True,
            "firebase": {},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.gcp_project_id = "pablohealth-dev"
            mock_settings.return_value.require_mfa = True
            mock_settings.return_value.auth_mode = "standard"
            mock_settings.return_value.e2e_test_emails = {"test@pablo.health"}
            result = require_mfa(MagicMock(), mock_credentials)

        assert result["uid"] == "e2e-user"

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_email_blocked_in_production(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "e2e-token"
        mock_verify.return_value = {
            "uid": "e2e-user",
            "email": "test@pablo.health",
            "email_verified": True,
            "firebase": {},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.gcp_project_id = "pablohealth-prod"
            mock_settings.return_value.require_mfa = True
            mock_settings.return_value.auth_mode = "standard"
            mock_settings.return_value.e2e_test_emails = {"test@pablo.health"}
            with pytest.raises(HTTPException) as exc_info:
                require_mfa(MagicMock(), mock_credentials)

        assert exc_info.value.detail["error"]["code"] == "MFA_REQUIRED"  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_email_blocked_when_not_verified(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "e2e-token"
        mock_verify.return_value = {
            "uid": "e2e-user",
            "email": "test@pablo.health",
            "email_verified": False,
            "firebase": {},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.gcp_project_id = "pablohealth-dev"
            mock_settings.return_value.require_mfa = True
            mock_settings.return_value.auth_mode = "standard"
            mock_settings.return_value.e2e_test_emails = {"test@pablo.health"}
            with pytest.raises(HTTPException) as exc_info:
                require_mfa(MagicMock(), mock_credentials)

        assert exc_info.value.detail["error"]["code"] == "MFA_REQUIRED"  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_bypass_ignores_unlisted_email(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "e2e-token"
        mock_verify.return_value = {
            "uid": "e2e-user",
            "email": "attacker@evil.com",
            "email_verified": True,
            "firebase": {},
        }

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.gcp_project_id = "pablohealth-dev"
            mock_settings.return_value.require_mfa = True
            mock_settings.return_value.auth_mode = "standard"
            mock_settings.return_value.e2e_test_emails = {"test@pablo.health"}
            with pytest.raises(HTTPException) as exc_info:
                require_mfa(MagicMock(), mock_credentials)

        assert exc_info.value.detail["error"]["code"] == "MFA_REQUIRED"  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_bypassed_when_require_mfa_false(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "no-mfa-token"
        mock_verify.return_value = {"uid": "user123", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            result = require_mfa(MagicMock(), mock_credentials)

        assert result["uid"] == "user123"


def _mock_request(tenant_id: str | None = None) -> MagicMock:
    """Create a mock Request with optional X-Tenant-ID header."""
    request = MagicMock()
    request.headers = {"X-Tenant-ID": tenant_id} if tenant_id else {}
    return request


class TestGetCurrentUser:
    """Test user lookup and auto-provisioning."""

    @patch("app.auth.service.verify_firebase_token")
    def test_auto_provisions_allowlisted_user(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {
            "uid": "new-user",
            "email": "allowed@example.com",
            "name": "New User",
            "firebase": {},
        }

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()
        allowlist_repo.add("allowed@example.com", "admin")

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = True
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True

            decoded = mock_verify.return_value
            user = get_current_user(_mock_request(), decoded, user_repo, allowlist_repo)

        assert user.id == "new-user"
        assert user.email == "allowed@example.com"
        assert user.status == "approved"

    @patch("app.auth.service.verify_firebase_token")
    def test_rejects_non_allowlisted_user(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {
            "uid": "blocked-user",
            "email": "notallowed@example.com",
            "firebase": {},
        }

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True

            decoded = mock_verify.return_value
            with pytest.raises(HTTPException) as exc_info:
                get_current_user(_mock_request(), decoded, user_repo, allowlist_repo)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "SIGNUP_NOT_ALLOWED"  # type: ignore[index]

    def test_rejects_disabled_user(self) -> None:
        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()

        disabled_user = User(
            id="disabled-user",
            email="disabled@example.com",
            name="Disabled User",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            status="disabled",
        )
        user_repo.update(disabled_user)

        decoded = {"uid": "disabled-user", "email": "disabled@example.com", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.restrict_signups = False

            with pytest.raises(HTTPException) as exc_info:
                get_current_user(_mock_request(), decoded, user_repo, allowlist_repo)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "USER_DISABLED"  # type: ignore[index]

    def test_existing_user_without_status_defaults_approved(self) -> None:
        """Existing records without status field default to approved."""
        data = {
            "id": "legacy-user",
            "email": "legacy@example.com",
            "name": "Legacy User",
            "created_at": "2024-01-01T00:00:00Z",
        }
        user = User.from_dict(data)
        assert user.status == "approved"


class TestTenantContext:
    """Test TenantContext data class."""

    def test_single_tenant_defaults(self) -> None:
        ctx = TenantContext(user_id="user123")
        assert ctx.user_id == "user123"
        assert ctx.practice_id is None
        assert ctx.practice_schema is None

    def test_practice_context(self) -> None:
        ctx = TenantContext(
            user_id="user123",
            practice_id="practice-a1b2c3",
            practice_schema="practice_a1b2c3",
        )
        assert ctx.practice_id == "practice-a1b2c3"
        assert ctx.practice_schema == "practice_a1b2c3"

    def test_frozen(self) -> None:
        ctx = TenantContext(user_id="user123")
        with pytest.raises(AttributeError):
            ctx.user_id = "other"  # type: ignore[misc]


class TestTokenVerificationWithTenantClaims:
    """Verify that tokens with legacy tenant claims still work.

    Even though tenant-scoped verification is removed, tokens may still
    contain firebase.tenant claims. These tests confirm that MFA and
    user ID extraction still work correctly with such tokens.
    """

    def test_mfa_works_with_tenant_claim(self) -> None:
        """MFA enforcement works identically for tenant-scoped tokens."""
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "tenant-mfa-token"

        with patch("app.auth.service.verify_firebase_token") as mock_verify:
            mock_verify.return_value = {
                "uid": "user123",
                "firebase": {
                    "tenant": "practice-a1b2c3",
                    "sign_in_second_factor": "totp",
                },
            }
            with patch("app.auth.service.get_settings") as mock_settings:
                mock_settings.return_value.is_development = False
                mock_settings.return_value.require_mfa = True
                result = require_mfa(MagicMock(), mock_credentials)

        assert result["firebase"]["tenant"] == "practice-a1b2c3"
        assert result["firebase"]["sign_in_second_factor"] == "totp"

    def test_mfa_rejects_tenant_token_without_second_factor(self) -> None:
        """Tenant-scoped token without MFA is rejected when MFA is required."""
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "tenant-no-mfa-token"

        with patch("app.auth.service.verify_firebase_token") as mock_verify:
            mock_verify.return_value = {
                "uid": "user123",
                "firebase": {
                    "tenant": "practice-a1b2c3",
                },
            }
            with patch("app.auth.service.get_settings") as mock_settings:
                mock_settings.return_value.is_development = False
                mock_settings.return_value.require_mfa = True
                with pytest.raises(HTTPException) as exc_info:
                    require_mfa(MagicMock(), mock_credentials)

        assert exc_info.value.detail["error"]["code"] == "MFA_REQUIRED"  # type: ignore[index]

    def test_user_id_extraction_from_tenant_token(self) -> None:
        """get_current_user_id works with tenant-scoped tokens."""
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "tenant-token"

        with patch("app.auth.service.verify_firebase_token") as mock_verify:
            mock_verify.return_value = {
                "uid": "user123",
                "firebase": {"tenant": "practice-a1b2c3"},
            }
            user_id = get_current_user_id(MagicMock(), mock_credentials)

        assert user_id == "user123"


class TestGetTenantContext:
    """Test the get_tenant_context FastAPI dependency.

    The context resolves via _resolve_practice_from_email (Postgres lookup).
    """

    def test_single_tenant_mode_returns_default(self) -> None:
        """When multi_tenancy_enabled=False, returns default context."""
        decoded = {"uid": "user123", "email": "dr@example.com", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False

            ctx = get_tenant_context(decoded, InMemoryUserRepository())

        assert ctx == TenantContext(user_id="user123")

    def test_resolves_practice_from_email(self) -> None:
        """Email resolved to practice via Postgres lookup."""
        decoded = {"uid": "user123", "email": "dr@example.com", "firebase": {}}

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-abc", "practice_abc"),
            ),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            # Set request-scoped DB session (normally done by middleware)
            mock_session = MagicMock()
            token = _request_session.set(mock_session)
            try:
                ctx = get_tenant_context(decoded, InMemoryUserRepository())
            finally:
                _request_session.reset(token)

        assert ctx == TenantContext(
            user_id="user123",
            practice_id="practice-abc",
            practice_schema="practice_abc",
        )

    def test_admin_without_practice_gets_default_context(self) -> None:
        """Platform admin with no practice mapping gets admin-only access."""
        decoded = {"uid": "admin-uid", "email": "admin@pablo.health", "firebase": {}}
        user_repo = InMemoryUserRepository()
        admin_user = User(
            id="admin-uid",
            email="admin@pablo.health",
            name="Admin",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            is_platform_admin=True,
        )
        user_repo.update(admin_user)

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._resolve_practice_from_email", return_value=None),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            ctx = get_tenant_context(decoded, user_repo)

        assert ctx == TenantContext(user_id="admin-uid")

    def test_rejects_non_admin_without_practice(self) -> None:
        """Non-admin user with no practice mapping is rejected."""
        decoded = {"uid": "user123", "email": "user@example.com", "firebase": {}}
        user_repo = InMemoryUserRepository()
        regular_user = User(
            id="user123",
            email="user@example.com",
            name="User",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        )
        user_repo.update(regular_user)

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._resolve_practice_from_email", return_value=None),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, user_repo)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "NO_PRACTICE"  # type: ignore[index]

    def test_rejects_unknown_user_without_practice(self) -> None:
        """Unknown user (not in repo) with no practice mapping is rejected."""
        decoded = {"uid": "unknown", "email": "unknown@example.com", "firebase": {}}

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._resolve_practice_from_email", return_value=None),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, InMemoryUserRepository())

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "NO_PRACTICE"  # type: ignore[index]

    def test_rejects_missing_uid(self) -> None:
        """Token without uid is rejected."""
        decoded = {"email": "dr@example.com", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, InMemoryUserRepository())

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]
