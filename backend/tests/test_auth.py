# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Firebase authentication and Identity Platform multi-tenancy."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.service import (
    TenantContext,
    _get_cached_token,
    _get_verifier_registry,
    get_current_user,
    get_current_user_id,
    get_tenant_context,
    require_mfa,
    verify_firebase_token,
)
from app.db import _current_user_id, _request_session
from app.models import User
from app.repositories import (
    InMemoryAllowlistRepository,
    InMemoryIdentityRepository,
    InMemoryUserRepository,
)
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from firebase_admin import auth as firebase_auth

VERIFY_PATCH = "app.auth.service.firebase_auth.verify_id_token"


def _identity_repo_for(*firebase_uids: str) -> InMemoryIdentityRepository:
    """Build an identity repo with legacy-backfilled mappings.

    Each uid is linked as ('firebase', uid, uid) — what the
    migration backfill produces for users provisioned before the
    indirection existed. Pass no uids to start empty.
    """
    repo = InMemoryIdentityRepository()
    for uid in firebase_uids:
        repo.link("firebase", uid, uid)
    return repo


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
            mock_settings.return_value.oidc_issuer = ""
            _get_verifier_registry.cache_clear()
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
            mock_settings.return_value.oidc_issuer = ""
            _get_verifier_registry.cache_clear()
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

        user_id = get_current_user_id(MagicMock(), mock_credentials, _identity_repo_for("user123"))

        assert user_id == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_missing_uid(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token-without-uid"
        mock_verify.return_value = {"email": "test@example.com"}

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_id(MagicMock(), mock_credentials, _identity_repo_for())

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
            get_current_user_id(MagicMock(), mock_credentials, _identity_repo_for())

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
            mock_settings.return_value.is_prod_project = False
            mock_settings.return_value.require_mfa = True
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
            mock_settings.return_value.is_prod_project = True
            mock_settings.return_value.require_mfa = True
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
            mock_settings.return_value.is_prod_project = False
            mock_settings.return_value.require_mfa = True
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
            mock_settings.return_value.is_prod_project = False
            mock_settings.return_value.require_mfa = True
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
        # Pre-seed mapping so the test exercises the legacy-backfill path
        # (stable id = firebase uid). A separate test covers the
        # fresh-signup case where a UUID is generated.
        identity_repo = _identity_repo_for("new-user")

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = True
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True

            decoded = mock_verify.return_value
            user = get_current_user(
                _mock_request(), decoded, user_repo, allowlist_repo, identity_repo
            )

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
        identity_repo = _identity_repo_for()

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._email_has_tenant_mapping", return_value=False),
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True

            decoded = mock_verify.return_value
            with pytest.raises(HTTPException) as exc_info:
                get_current_user(_mock_request(), decoded, user_repo, allowlist_repo, identity_repo)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "SIGNUP_NOT_ALLOWED"  # type: ignore[index]
        # Allowlist gate keeps rejected users out of the mapping table
        assert identity_repo.get_user_id("firebase", "blocked-user") is None

    @patch("app.auth.service.verify_firebase_token")
    def test_allows_provisioned_tenant_without_explicit_allowlist(
        self, mock_verify: MagicMock
    ) -> None:
        """A user with an EmailTenantMappingRow but no allowed_emails row passes.

        Mirrors the implicit-allowlist fallback in /api/ext/auth/check-allowlist
        so the marketing-signup -> provisioned-tenant flow can actually use
        the app after sign-up (THERAPY-glzf). Without this, blocking-fn lets
        them sign up but the token-auth middleware 403s every API call.
        """
        mock_verify.return_value = {
            "uid": "self-serve-user",
            "email": "owner@newpractice.com",
            "firebase": {},
        }

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()  # no entry
        identity_repo = _identity_repo_for()

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch(
                "app.auth.service._email_has_tenant_mapping", return_value=True
            ) as mock_tenant_lookup,
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True

            decoded = mock_verify.return_value
            user = get_current_user(
                _mock_request(), decoded, user_repo, allowlist_repo, identity_repo
            )

        assert user.email == "owner@newpractice.com"
        assert user.status == "approved"
        mock_tenant_lookup.assert_called_once_with("owner@newpractice.com")

    @patch("app.auth.service.verify_firebase_token")
    def test_skips_tenant_fallback_when_multi_tenancy_disabled(
        self, mock_verify: MagicMock
    ) -> None:
        """Single-tenant deployments must not honor the mapping fallback.

        EmailTenantMappingRow is not meaningful when multi-tenancy is off,
        so the explicit allowlist remains the only gate.
        """
        mock_verify.return_value = {
            "uid": "stranger",
            "email": "stranger@example.com",
            "firebase": {},
        }

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()
        identity_repo = _identity_repo_for()

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch(
                "app.auth.service._email_has_tenant_mapping", return_value=True
            ) as mock_tenant_lookup,
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = False

            decoded = mock_verify.return_value
            with pytest.raises(HTTPException) as exc_info:
                get_current_user(_mock_request(), decoded, user_repo, allowlist_repo, identity_repo)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "SIGNUP_NOT_ALLOWED"  # type: ignore[index]
        # Single-tenant deployments must not even consult the mapping table.
        mock_tenant_lookup.assert_not_called()

    @patch("app.auth.service.verify_firebase_token")
    def test_allows_e2e_prefixed_user_without_allowlist(self, mock_verify: MagicMock) -> None:
        """Reserved e2etest-<8hex>@pablo.health prefix bypasses the allowlist.

        Mirrors the existing pentest bypass. Used by pablo-saas/e2e Cloud
        Run Job so the test runner doesn't need write access to
        platform.allowed_emails (THERAPY-wy0f).
        """
        mock_verify.return_value = {
            "uid": "e2e-user",
            "email": "e2etest-deadbeef@pablo.health",
            "firebase": {},
        }

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()  # no entry
        identity_repo = _identity_repo_for()

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._email_has_tenant_mapping", return_value=False),
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_prod_project = False

            decoded = mock_verify.return_value
            user = get_current_user(
                _mock_request(), decoded, user_repo, allowlist_repo, identity_repo
            )

        assert user.email == "e2etest-deadbeef@pablo.health"
        assert user.status == "approved"

    @patch("app.auth.service.verify_firebase_token")
    def test_rejects_malformed_e2e_prefix(self, mock_verify: MagicMock) -> None:
        """Only the exact e2etest-<8hex>@pablo.health pattern bypasses.

        Adjacent variants (wrong hex length, wrong domain, prefix
        in the middle) still hit the allowlist gate. Prevents a real
        user from accidentally matching by claiming an e2etest-ish email.
        """
        # Uppercase isn't tested here — _extract_email() lowercases first,
        # so DEADBEEF would correctly match (and that's intentional; emails
        # are case-insensitive). See test_e2e_prefix_matches_case_insensitively
        # below for the positive uppercase case.
        for email in (
            "e2etest-deadbee@pablo.health",  # 7 hex
            "e2etest-deadbeef0@pablo.health",  # 9 hex
            "e2etest-deadxxxx@pablo.health",  # non-hex
            "e2etest-deadbeef@example.com",  # wrong domain
            "real-e2etest-deadbeef@pablo.health",  # prefix not at start
        ):
            mock_verify.return_value = {
                "uid": f"fake-{email}",
                "email": email,
                "firebase": {},
            }

            with (
                patch("app.auth.service.get_settings") as mock_settings,
                patch("app.auth.service._email_has_tenant_mapping", return_value=False),
            ):
                mock_settings.return_value.is_development = False
                mock_settings.return_value.require_mfa = False
                mock_settings.return_value.restrict_signups = True
                mock_settings.return_value.multi_tenancy_enabled = True
                mock_settings.return_value.is_prod_project = False

                with pytest.raises(HTTPException) as exc_info:
                    get_current_user(
                        _mock_request(),
                        mock_verify.return_value,
                        InMemoryUserRepository(),
                        InMemoryAllowlistRepository(),
                        _identity_repo_for(),
                    )

            assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN, (
                f"{email!r} unexpectedly allowed"
            )

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_prefix_matches_case_insensitively(self, mock_verify: MagicMock) -> None:
        """_extract_email lowercases before regex match — uppercase passes."""
        mock_verify.return_value = {
            "uid": "e2e-upper",
            "email": "E2EtEsT-DEADBEEF@pablo.health",
            "firebase": {},
        }

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._email_has_tenant_mapping", return_value=False),
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_prod_project = False

            user = get_current_user(
                _mock_request(),
                mock_verify.return_value,
                InMemoryUserRepository(),
                InMemoryAllowlistRepository(),
                _identity_repo_for(),
            )

        # _extract_email lowercased the stored email
        assert user.email == "e2etest-deadbeef@pablo.health"

    @patch("app.auth.service.verify_firebase_token")
    def test_e2e_prefix_rejected_in_prod(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {
            "uid": "e2e-prod",
            "email": "e2etest-deadbeef@pablo.health",
            "firebase": {},
        }

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._email_has_tenant_mapping", return_value=False),
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_prod_project = True

            with pytest.raises(HTTPException) as exc_info:
                get_current_user(
                    _mock_request(),
                    mock_verify.return_value,
                    InMemoryUserRepository(),
                    InMemoryAllowlistRepository(),
                    _identity_repo_for(),
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "SIGNUP_NOT_ALLOWED"  # type: ignore[index]

    @patch("app.auth.service.verify_firebase_token")
    def test_pentestuser_prefix_rejected_in_prod(self, mock_verify: MagicMock) -> None:
        mock_verify.return_value = {
            "uid": "pentest-prod",
            "email": "pentestuser-cafebabe@pablo.health",
            "firebase": {},
        }

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service._email_has_tenant_mapping", return_value=False),
        ):
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.is_prod_project = True

            with pytest.raises(HTTPException) as exc_info:
                get_current_user(
                    _mock_request(),
                    mock_verify.return_value,
                    InMemoryUserRepository(),
                    InMemoryAllowlistRepository(),
                    _identity_repo_for(),
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "SIGNUP_NOT_ALLOWED"  # type: ignore[index]

    def test_rejects_disabled_user(self) -> None:
        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()
        identity_repo = _identity_repo_for("disabled-user")

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
                get_current_user(_mock_request(), decoded, user_repo, allowlist_repo, identity_repo)

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
            user_id = get_current_user_id(
                MagicMock(), mock_credentials, _identity_repo_for("user123")
            )

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

            ctx = get_tenant_context(
                _mock_request(),
                decoded,
                InMemoryUserRepository(),
                _identity_repo_for("user123"),
            )

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
            # The post-resolve provisioning_status gate (THERAPY-da7t)
            # queries platform.practices via a fresh standalone session;
            # this unit test mocks the upstream resolver and has no real
            # platform row to find, so skip the gate here. Integration
            # tests exercise the gate end-to-end.
            patch("app.auth.service._await_provisioning_ready"),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            # Set request-scoped DB session (normally done by middleware)
            mock_session = MagicMock()
            token = _request_session.set(mock_session)
            user_id_token = _current_user_id.set(None)
            try:
                ctx = get_tenant_context(
                    _mock_request(),
                    decoded,
                    InMemoryUserRepository(),
                    _identity_repo_for("user123"),
                )

                # Returning the right TenantContext is not enough — the
                # function must actually ARM RLS, or queries silently return
                # zero rows under row-level security. Assert the side effects,
                # so a regression that drops the RLS-arming arm fails loudly.
                # 1. set_current_user_id stashed the id in the ContextVar the
                #    after_begin listener re-arms the GUC from on each txn.
                assert _current_user_id.get() == "user123"
                # 2. The app.current_user_id GUC was set on the live session.
                guc_calls = [
                    ex
                    for ex in mock_session.execute.call_args_list
                    if "set_config" in str(ex.args[0]) and "app.current_user_id" in str(ex.args[0])
                ]
                assert guc_calls, "get_tenant_context did not arm the RLS GUC"
                assert guc_calls[0].args[1] == {"uid": "user123"}
            finally:
                _request_session.reset(token)
                _current_user_id.reset(user_id_token)

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

            ctx = get_tenant_context(
                _mock_request(), decoded, user_repo, _identity_repo_for("admin-uid")
            )

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
                get_tenant_context(
                    _mock_request(), decoded, user_repo, _identity_repo_for("user123")
                )

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
                get_tenant_context(
                    _mock_request(),
                    decoded,
                    InMemoryUserRepository(),
                    _identity_repo_for(),
                )

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "NO_PRACTICE"  # type: ignore[index]

    def test_rejects_missing_uid(self) -> None:
        """Token without uid is rejected."""
        decoded = {"email": "dr@example.com", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(
                    _mock_request(),
                    decoded,
                    InMemoryUserRepository(),
                    _identity_repo_for(),
                )

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]


class TestUserIdentityMapping:
    """Test the (provider, subject_id) -> user_id indirection.

    The mapping decouples Pablo's storage identity from a single auth
    provider's subject — once user_identities holds the mapping, the
    rest of the system never sees the provider's raw uid.
    """

    @patch("app.auth.service.verify_firebase_token")
    def test_lookup_returns_mapped_pablo_user_id(self, mock_verify: MagicMock) -> None:
        """An existing mapping translates Firebase uid to the Pablo uuid."""
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token"
        mock_verify.return_value = {"uid": "firebase-uid-abc"}

        identity_repo = InMemoryIdentityRepository()
        identity_repo.link("firebase", "firebase-uid-abc", "uuid-pablo-id-1")

        result = get_current_user_id(MagicMock(), mock_credentials, identity_repo)

        assert result == "uuid-pablo-id-1"

    @patch("app.auth.service.verify_firebase_token")
    def test_legacy_user_falls_back_to_firebase_uid(self, mock_verify: MagicMock) -> None:
        """No mapping → fall back to the Firebase uid (legacy compat).

        Users provisioned before the mapping existed get a backfilled row
        in the migration. Until that runs, lookup-only paths return the
        Firebase uid so existing FK references still resolve.
        """
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token"
        mock_verify.return_value = {"uid": "legacy-firebase-uid"}

        identity_repo = InMemoryIdentityRepository()  # empty

        result = get_current_user_id(MagicMock(), mock_credentials, identity_repo)

        assert result == "legacy-firebase-uid"
        # Lookup-only path must not mint a mapping for unauthenticated /
        # un-allowlisted users — that's the auto-provision path's job.
        assert identity_repo.get_user_id("firebase", "legacy-firebase-uid") is None

    @patch("app.auth.service.verify_firebase_token")
    def test_auto_provision_creates_mapping_with_new_uuid(self, mock_verify: MagicMock) -> None:
        """First-time sign-up: a fresh UUID is minted and linked."""
        mock_verify.return_value = {
            "uid": "fresh-firebase-uid",
            "email": "fresh@example.com",
            "name": "Fresh User",
            "firebase": {},
        }
        decoded = mock_verify.return_value

        user_repo = InMemoryUserRepository()
        allowlist_repo = InMemoryAllowlistRepository()
        allowlist_repo.add("fresh@example.com", "admin")
        identity_repo = InMemoryIdentityRepository()  # empty

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = True
            mock_settings.return_value.require_mfa = False
            mock_settings.return_value.restrict_signups = True

            user = get_current_user(
                _mock_request(), decoded, user_repo, allowlist_repo, identity_repo
            )

        mapped = identity_repo.get_user_id("firebase", "fresh-firebase-uid")
        assert mapped is not None
        assert mapped == user.id
        # The stored id is the Pablo uuid, not the Firebase uid
        assert user.id != "fresh-firebase-uid"
        # Sanity: UUID4 string form
        assert len(user.id) == 36
        assert user.id.count("-") == 4

    @patch("app.auth.service.verify_firebase_token")
    def test_resolution_caches_on_request_state(self, mock_verify: MagicMock) -> None:
        """Multiple resolves within one request hit the cache, not the repo."""
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token"
        mock_verify.return_value = {"uid": "cached-uid"}

        identity_repo = InMemoryIdentityRepository()
        identity_repo.link("firebase", "cached-uid", "pablo-cached-uuid")

        request = MagicMock()
        request.state = SimpleNamespace()
        # First call populates the cache via the repo
        first = get_current_user_id(request, mock_credentials, identity_repo)
        # Mutate the repo: if the second call hit the repo it would now miss
        identity_repo._mappings.clear()
        # Provide a fresh decoded token so verify_firebase_token isn't reused
        request.state.verified_firebase_token_raw = "token"
        request.state.decoded_firebase_token = {"uid": "cached-uid"}
        second = get_current_user_id(request, mock_credentials, identity_repo)

        assert first == second == "pablo-cached-uuid"


class TestIdentityReverseLookup:
    """Unit tests for IdentityRepository.get_subject_id (THERAPY-glzf-2)."""

    def test_returns_subject_id_when_linked(self) -> None:
        repo = InMemoryIdentityRepository()
        repo.link("firebase", "firebase-uid-xyz", "pablo-uuid-1")

        assert repo.get_subject_id("pablo-uuid-1", "firebase") == "firebase-uid-xyz"

    def test_returns_none_when_user_has_no_mapping(self) -> None:
        repo = InMemoryIdentityRepository()  # empty

        assert repo.get_subject_id("pablo-uuid-unknown", "firebase") is None

    def test_filters_by_provider(self) -> None:
        """A user_id linked under provider A must not surface under provider B."""
        repo = InMemoryIdentityRepository()
        repo.link("firebase", "subject-a", "pablo-uuid-1")

        assert repo.get_subject_id("pablo-uuid-1", "firebase") == "subject-a"
        assert repo.get_subject_id("pablo-uuid-1", "google-oauth") is None

    def test_round_trip_with_get_user_id(self) -> None:
        """Forward and reverse lookups agree on the linked pair."""
        repo = InMemoryIdentityRepository()
        repo.link("firebase", "round-trip-subject", "round-trip-user")

        forward = repo.get_user_id("firebase", "round-trip-subject")
        reverse = repo.get_subject_id("round-trip-user", "firebase")

        assert forward == "round-trip-user"
        assert reverse == "round-trip-subject"
