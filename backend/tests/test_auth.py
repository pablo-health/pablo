# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Firebase authentication and Identity Platform multi-tenancy."""

from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.service import (
    TenantContext,
    clear_tenant_cache,
    extract_tenant_id,
    get_current_user,
    get_current_user_id,
    get_tenant_context,
    require_mfa,
    resolve_tenant_database,
    verify_firebase_token,
)
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


class TestGetCurrentUserId:
    """Test user ID extraction from Firebase token."""

    @patch("app.auth.service.verify_firebase_token")
    def test_extract_user_id(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "valid-token"
        mock_verify.return_value = {"uid": "user123", "email": "test@example.com"}

        user_id = get_current_user_id(mock_credentials)

        assert user_id == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_missing_uid(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "token-without-uid"
        mock_verify.return_value = {"email": "test@example.com"}

        with pytest.raises(HTTPException) as exc_info:
            get_current_user_id(mock_credentials)

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
            get_current_user_id(mock_credentials)

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
            result = require_mfa(mock_credentials)

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
                require_mfa(mock_credentials)

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
            result = require_mfa(mock_credentials)

        assert result["uid"] == "user123"

    @patch("app.auth.service.verify_firebase_token")
    def test_bypassed_when_require_mfa_false(self, mock_verify: MagicMock) -> None:
        mock_credentials = Mock(spec=HTTPAuthorizationCredentials)
        mock_credentials.credentials = "no-mfa-token"
        mock_verify.return_value = {"uid": "user123", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.is_development = False
            mock_settings.return_value.require_mfa = False
            result = require_mfa(mock_credentials)

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
            created_at="2024-01-01T00:00:00Z",
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
        """Existing Firestore docs without status field default to approved."""
        data = {
            "id": "legacy-user",
            "email": "legacy@example.com",
            "name": "Legacy User",
            "created_at": "2024-01-01T00:00:00Z",
        }
        user = User.from_dict(data)
        assert user.status == "approved"


class TestExtractTenantId:
    """Test tenant ID extraction from Identity Platform tokens."""

    def test_extracts_tenant_from_token(self) -> None:
        """Tenant-scoped tokens include firebase.tenant claim."""
        decoded = {
            "uid": "user123",
            "email": "dr.smith@gmail.com",
            "firebase": {
                "tenant": "practice-a1b2c3",
                "sign_in_provider": "google.com",
            },
        }
        assert extract_tenant_id(decoded) == "practice-a1b2c3"

    def test_returns_none_for_non_tenant_token(self) -> None:
        """Non-tenant tokens (single-tenant mode) have no tenant claim."""
        decoded = {
            "uid": "user123",
            "email": "dr.smith@gmail.com",
            "firebase": {
                "sign_in_provider": "google.com",
            },
        }
        assert extract_tenant_id(decoded) is None

    def test_returns_none_for_empty_firebase_claims(self) -> None:
        decoded = {"uid": "user123", "firebase": {}}
        assert extract_tenant_id(decoded) is None

    def test_returns_none_when_firebase_key_missing(self) -> None:
        decoded = {"uid": "user123"}
        assert extract_tenant_id(decoded) is None


class TestTenantContext:
    """Test TenantContext data class."""

    def test_single_tenant_defaults(self) -> None:
        ctx = TenantContext(user_id="user123")
        assert ctx.user_id == "user123"
        assert ctx.tenant_id is None
        assert ctx.firestore_db == "(default)"

    def test_multi_tenant_context(self) -> None:
        ctx = TenantContext(
            user_id="user123",
            tenant_id="practice-a1b2c3",
            firestore_db="tenant-a1b2c3",
        )
        assert ctx.tenant_id == "practice-a1b2c3"
        assert ctx.firestore_db == "tenant-a1b2c3"

    def test_frozen(self) -> None:
        ctx = TenantContext(user_id="user123")
        with pytest.raises(AttributeError):
            ctx.user_id = "other"  # type: ignore[misc]


class TestTokenVerificationWithTenantClaims:
    """Verify that verify_id_token handles tenant-scoped tokens correctly.

    Identity Platform tokens with tenant claims are verified identically
    to non-tenant tokens — firebase_admin.auth.verify_id_token() handles
    both transparently. These tests confirm backward compatibility.
    """

    def test_tenant_scoped_token_verified_normally(self, mock_firebase_init: Any) -> None:
        """Token with tenant claim is verified the same as any other token."""
        tenant_token_claims = {
            "uid": "user123",
            "email": "dr.smith@gmail.com",
            "firebase": {
                "tenant": "practice-a1b2c3",
                "sign_in_provider": "google.com",
                "sign_in_second_factor": "totp",
            },
        }

        with patch(VERIFY_PATCH) as mock_verify:
            mock_verify.return_value = tenant_token_claims

            result = verify_firebase_token("tenant-scoped-token")

            assert result["uid"] == "user123"
            assert result["firebase"]["tenant"] == "practice-a1b2c3"
            mock_verify.assert_called_once_with("tenant-scoped-token", check_revoked=True)
            mock_firebase_init.assert_called_once()

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
                result = require_mfa(mock_credentials)

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
                    require_mfa(mock_credentials)

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
            user_id = get_current_user_id(mock_credentials)

        assert user_id == "user123"


class TestResolveTenantDatabase:
    """Test tenant→database resolution with caching."""

    def setup_method(self) -> None:
        clear_tenant_cache()

    def test_resolves_tenant_to_database(self) -> None:
        admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"firestore_database": "tenant-abc123"}
        admin_db.collection("tenants").document("practice-abc").get.return_value = doc

        result = resolve_tenant_database("practice-abc", admin_db)

        assert result == "tenant-abc123"

    def test_returns_none_for_unknown_tenant(self) -> None:
        admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = False
        admin_db.collection("tenants").document("unknown").get.return_value = doc

        result = resolve_tenant_database("unknown", admin_db)

        assert result is None

    def test_caches_resolved_tenant(self) -> None:
        admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"firestore_database": "tenant-cached"}
        admin_db.collection("tenants").document("practice-cached").get.return_value = doc

        # First call hits DB
        resolve_tenant_database("practice-cached", admin_db)
        # Second call should use cache
        result = resolve_tenant_database("practice-cached", admin_db)

        assert result == "tenant-cached"
        # Only one DB call despite two resolves
        admin_db.collection("tenants").document("practice-cached").get.assert_called_once()

    def test_cache_expires(self) -> None:
        admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"firestore_database": "tenant-ttl"}
        admin_db.collection("tenants").document("practice-ttl").get.return_value = doc

        # t=0: write cache, t=400: expired, t=400: rewrite cache
        with patch("app.auth.service.time.monotonic", side_effect=[0, 400, 400]):
            resolve_tenant_database("practice-ttl", admin_db)
            # Second call — cache expired (monotonic jumped past TTL)
            resolve_tenant_database("practice-ttl", admin_db)

        assert admin_db.collection("tenants").document("practice-ttl").get.call_count == 2


class TestGetTenantContext:
    """Test the get_tenant_context FastAPI dependency."""

    def test_single_tenant_mode_returns_default(self) -> None:
        """When multi_tenancy_enabled=False, returns default context."""
        decoded = {"uid": "user123", "firebase": {"tenant": "practice-abc"}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = False

            ctx = get_tenant_context(decoded, InMemoryUserRepository())

        assert ctx == TenantContext(user_id="user123")

    def test_multi_tenant_resolves_tenant_to_db(self) -> None:
        """Tenant claim resolved to database name."""
        decoded = {"uid": "user123", "firebase": {"tenant": "practice-abc"}}
        clear_tenant_cache()

        mock_admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = {"firestore_database": "tenant-abc"}
        mock_admin_db.collection("tenants").document("practice-abc").get.return_value = doc

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service.get_admin_firestore_client", return_value=mock_admin_db),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            ctx = get_tenant_context(decoded, InMemoryUserRepository())

        assert ctx == TenantContext(
            user_id="user123",
            tenant_id="practice-abc",
            firestore_db="tenant-abc",
        )

    def test_admin_without_tenant_gets_admin_db(self) -> None:
        """Platform admin (no tenant claim + is_admin) gets admin-only access."""
        decoded = {"uid": "admin-uid", "firebase": {}}
        user_repo = InMemoryUserRepository()
        admin_user = User(
            id="admin-uid",
            email="admin@pablo.health",
            name="Admin",
            created_at="2024-01-01T00:00:00Z",
            is_admin=True,
        )
        user_repo.update(admin_user)

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True
            mock_settings.return_value.admin_database = "(default)"

            ctx = get_tenant_context(decoded, user_repo)

        assert ctx == TenantContext(user_id="admin-uid", firestore_db="(default)")

    def test_rejects_non_admin_without_tenant(self) -> None:
        """Non-admin user with no tenant claim is rejected."""
        decoded = {"uid": "user123", "firebase": {}}
        user_repo = InMemoryUserRepository()
        regular_user = User(
            id="user123",
            email="user@example.com",
            name="User",
            created_at="2024-01-01T00:00:00Z",
        )
        user_repo.update(regular_user)

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, user_repo)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "MISSING_TENANT"  # type: ignore[index]

    def test_rejects_unknown_user_without_tenant(self) -> None:
        """Unknown user (not in repo) with no tenant claim is rejected."""
        decoded = {"uid": "unknown", "firebase": {}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, InMemoryUserRepository())

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "MISSING_TENANT"  # type: ignore[index]

    def test_rejects_unknown_tenant(self) -> None:
        """Valid tenant claim but tenant not in admin DB returns 403."""
        decoded = {"uid": "user123", "firebase": {"tenant": "nonexistent"}}
        clear_tenant_cache()

        mock_admin_db = MagicMock()
        doc = MagicMock()
        doc.exists = False
        mock_admin_db.collection("tenants").document("nonexistent").get.return_value = doc

        with (
            patch("app.auth.service.get_settings") as mock_settings,
            patch("app.auth.service.get_admin_firestore_client", return_value=mock_admin_db),
        ):
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, InMemoryUserRepository())

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert exc_info.value.detail["error"]["code"] == "UNKNOWN_TENANT"  # type: ignore[index]

    def test_rejects_missing_uid(self) -> None:
        """Token without uid is rejected."""
        decoded = {"firebase": {"tenant": "practice-abc"}}

        with patch("app.auth.service.get_settings") as mock_settings:
            mock_settings.return_value.multi_tenancy_enabled = True

            with pytest.raises(HTTPException) as exc_info:
                get_tenant_context(decoded, InMemoryUserRepository())

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc_info.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]
