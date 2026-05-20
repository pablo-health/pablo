# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the Firebase blocking-function OIDC verifier."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.routes.ext_auth import (
    CheckAllowlistRequest,
    _verify_blocking_function_token,
    check_allowlist,
)
from app.settings import Settings
from fastapi import HTTPException, Request

BACKEND_URL = "https://pablo-backend-test-uc.a.run.app"
BLOCKING_FN_SA = "firebase-blocking-fn@pablo-test.iam.gserviceaccount.com"


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI Request with the given headers."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/ext/auth/check-allowlist",
        "headers": raw_headers,
    }
    return Request(scope)


def _prod_settings(**overrides: Any) -> Settings:
    return Settings(
        environment="production",
        database_url="postgresql://x:x@localhost:5432/x",
        backend_base_url=overrides.pop("backend_base_url", BACKEND_URL),
        blocking_function_service_account=overrides.pop(
            "blocking_function_service_account", BLOCKING_FN_SA
        ),
        **overrides,
    )


def _valid_claims(**overrides: Any) -> dict[str, Any]:
    return {
        "iss": "https://accounts.google.com",
        "aud": BACKEND_URL,
        "email": BLOCKING_FN_SA,
        "email_verified": True,
        **overrides,
    }


@pytest.fixture
def patch_settings() -> Any:
    """Patch get_settings in the ext_auth module."""
    with patch("app.routes.ext_auth.get_settings") as mock:
        yield mock


@pytest.fixture
def patch_verify() -> Any:
    """Patch google.oauth2.id_token.verify_token."""
    with patch("app.routes.ext_auth.google.oauth2.id_token.verify_token") as mock:
        yield mock


def test_dev_mode_skips_auth_entirely(patch_settings: MagicMock) -> None:
    patch_settings.return_value = Settings(environment="development")
    # No Authorization header, but dev mode should short-circuit.
    _verify_blocking_function_token(_make_request())


def test_missing_authorization_header_rejected(patch_settings: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request())
    assert exc.value.status_code == 403


def test_non_bearer_authorization_rejected(patch_settings: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Basic abc"}))
    assert exc.value.status_code == 403


def test_invalid_signature_rejected(patch_settings: MagicMock, patch_verify: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.side_effect = ValueError("invalid signature")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_wrong_audience_rejected(patch_settings: MagicMock, patch_verify: MagicMock) -> None:
    """verify_token raises when audience mismatches — we must propagate 403."""
    patch_settings.return_value = _prod_settings()
    patch_verify.side_effect = ValueError("audience mismatch")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403
    _, kwargs = patch_verify.call_args
    assert kwargs["audience"] == BACKEND_URL


def test_wrong_issuer_rejected(patch_settings: MagicMock, patch_verify: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims(iss="https://evil.example.com")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_email_not_verified_rejected(patch_settings: MagicMock, patch_verify: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims(email_verified=False)
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_wrong_caller_service_account_rejected(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims(
        email="attacker@other-project.iam.gserviceaccount.com",
    )
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_happy_path_all_checks_pass(patch_settings: MagicMock, patch_verify: MagicMock) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims()
    _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    # Confirm audience was pinned on the call.
    _, kwargs = patch_verify.call_args
    assert kwargs["audience"] == BACKEND_URL


def test_unset_audience_fails_closed_with_503(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    """If backend_base_url is empty in production, the endpoint refuses to
    authenticate anything. Any Google-signed OIDC token would otherwise
    satisfy signature + issuer + email_verified, so fail closed.
    """
    patch_settings.return_value = _prod_settings(backend_base_url="")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 503
    # verify_token must not have been called — we reject before the crypto step.
    patch_verify.assert_not_called()


def test_unset_caller_sa_fails_closed_with_503(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    """If blocking_function_service_account is empty, we don't know which
    caller we're expecting — refuse the request rather than accept any
    Google-signed SA."""
    patch_settings.return_value = _prod_settings(blocking_function_service_account="")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 503
    patch_verify.assert_not_called()


# ── check_allowlist handler behavior ─────────────────────────────────────


def _dev_settings(**overrides: Any) -> Settings:
    """Dev settings short-circuit OIDC verification, so the handler runs."""
    return Settings(
        environment="development",
        database_url="postgresql://x:x@localhost:5432/x",
        restrict_signups=overrides.pop("restrict_signups", True),
        multi_tenancy_enabled=overrides.pop("multi_tenancy_enabled", True),
        **overrides,
    )


@pytest.fixture
def patch_allowlist_repo() -> Any:
    with patch("app.routes.ext_auth.get_allowlist_repository") as mock:
        yield mock


@pytest.fixture
def patch_db_session() -> Any:
    with patch("app.routes.ext_auth.get_db_session") as mock:
        yield mock


def test_check_allowlist_restrict_off_allows_all(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings(restrict_signups=False)
    result = check_allowlist(CheckAllowlistRequest(email="anyone@example.com"), _make_request())
    assert result.allowed is True
    patch_allowlist_repo.assert_not_called()


def test_check_allowlist_explicit_hit_returns_true(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings()
    repo = MagicMock()
    repo.is_allowed.return_value = True
    patch_allowlist_repo.return_value = repo

    result = check_allowlist(CheckAllowlistRequest(email="known@example.com"), _make_request())
    assert result.allowed is True
    # When explicit allowlist matches, we don't fall through to the
    # tenant-mapping check.
    patch_db_session.assert_not_called()


def test_check_allowlist_tenant_mapping_acts_as_implicit_allowlist(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    """Provisioned tenants should pass even without an explicit allowlist row.

    Self-serve signup populates EmailTenantMappingRow but not
    platform.allowed_emails — without this fallback, restrict_signups=True
    would lock out users immediately after provisioning their own tenant.
    """
    patch_settings.return_value = _dev_settings()
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo

    session = MagicMock()
    session.get.return_value = MagicMock()  # EmailTenantMappingRow exists
    patch_db_session.return_value = session

    result = check_allowlist(CheckAllowlistRequest(email="NewUser@Example.com"), _make_request())
    assert result.allowed is True
    # Mapping lookups are lowercased to match storage.
    args, _ = session.get.call_args
    assert args[1] == "newuser@example.com"


def test_check_allowlist_no_explicit_no_mapping_rejected(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings()
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo

    session = MagicMock()
    session.get.return_value = None
    patch_db_session.return_value = session

    result = check_allowlist(CheckAllowlistRequest(email="stranger@example.com"), _make_request())
    assert result.allowed is False


def test_check_allowlist_e2etest_prefix_bypasses(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    """Reserved e2etest-<8hex>@pablo.health bypasses allowlist + tenant lookup."""
    patch_settings.return_value = _dev_settings()
    result = check_allowlist(
        CheckAllowlistRequest(email="e2etest-deadbeef@pablo.health"), _make_request()
    )
    assert result.allowed is True
    # Bypass short-circuits before hitting the repo or DB
    patch_allowlist_repo.assert_not_called()
    patch_db_session.assert_not_called()


def test_check_allowlist_pentestuser_prefix_bypasses(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    """Reserved pentestuser-<8hex>@pablo.health also bypasses (parity check)."""
    patch_settings.return_value = _dev_settings()
    result = check_allowlist(
        CheckAllowlistRequest(email="pentestuser-cafebabe@pablo.health"),
        _make_request(),
    )
    assert result.allowed is True
    patch_allowlist_repo.assert_not_called()
    patch_db_session.assert_not_called()


def test_check_allowlist_e2etest_lookalike_does_not_bypass(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    """Wrong-shape e2etest emails fall through to the normal gate."""
    patch_settings.return_value = _dev_settings()
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo
    session = MagicMock()
    session.get.return_value = None
    patch_db_session.return_value = session

    for email in (
        "e2etest-deadbee@pablo.health",  # 7 hex
        "e2etest-deadbeef@example.com",  # wrong domain
        "user-e2etest-deadbeef@pablo.health",  # prefix not at start
    ):
        result = check_allowlist(CheckAllowlistRequest(email=email), _make_request())
        assert result.allowed is False, f"{email!r} should not bypass"


def test_check_allowlist_skips_mapping_fallback_when_multi_tenancy_off(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    """In single-tenant deployments, EmailTenantMappingRow isn't meaningful;
    don't bypass the explicit allowlist."""
    patch_settings.return_value = _dev_settings(multi_tenancy_enabled=False)
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo

    result = check_allowlist(CheckAllowlistRequest(email="stranger@example.com"), _make_request())
    assert result.allowed is False
    patch_db_session.assert_not_called()


def test_check_allowlist_e2etest_prefix_rejected_in_prod(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings(gcp_project_id="pablohealth-prod")
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo
    session = MagicMock()
    session.get.return_value = None
    patch_db_session.return_value = session

    result = check_allowlist(
        CheckAllowlistRequest(email="e2etest-deadbeef@pablo.health"),
        _make_request(),
    )
    assert result.allowed is False
    repo.is_allowed.assert_called_once()


def test_check_allowlist_pentestuser_prefix_rejected_in_prod(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings(gcp_project_id="pablohealth-prod")
    repo = MagicMock()
    repo.is_allowed.return_value = False
    patch_allowlist_repo.return_value = repo
    session = MagicMock()
    session.get.return_value = None
    patch_db_session.return_value = session

    result = check_allowlist(
        CheckAllowlistRequest(email="pentestuser-cafebabe@pablo.health"),
        _make_request(),
    )
    assert result.allowed is False
    repo.is_allowed.assert_called_once()


def test_check_allowlist_e2etest_prefix_still_bypasses_in_pentest_project(
    patch_settings: MagicMock,
    patch_allowlist_repo: MagicMock,
    patch_db_session: MagicMock,
) -> None:
    patch_settings.return_value = _dev_settings(gcp_project_id="pablohealth-pentest")
    result = check_allowlist(
        CheckAllowlistRequest(email="e2etest-deadbeef@pablo.health"),
        _make_request(),
    )
    assert result.allowed is True
    patch_allowlist_repo.assert_not_called()
    patch_db_session.assert_not_called()
