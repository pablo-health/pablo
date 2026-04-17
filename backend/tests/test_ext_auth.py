# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the Firebase blocking-function OIDC verifier."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.routes.ext_auth import _verify_blocking_function_token
from app.settings import Settings
from fastapi import HTTPException, Request

BACKEND_URL = "https://pablo-backend-test-uc.a.run.app"
BLOCKING_FN_SA = "firebase-blocking-fn@pablo-test.iam.gserviceaccount.com"


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal ASGI Request with the given headers."""
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
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


def test_invalid_signature_rejected(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.side_effect = ValueError("invalid signature")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_wrong_audience_rejected(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    """verify_token raises when audience mismatches — we must propagate 403."""
    patch_settings.return_value = _prod_settings()
    patch_verify.side_effect = ValueError("audience mismatch")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403
    _, kwargs = patch_verify.call_args
    assert kwargs["audience"] == BACKEND_URL


def test_wrong_issuer_rejected(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims(iss="https://evil.example.com")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403


def test_email_not_verified_rejected(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
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


def test_happy_path_all_checks_pass(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings()
    patch_verify.return_value = _valid_claims()
    _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    # Confirm audience was pinned on the call.
    _, kwargs = patch_verify.call_args
    assert kwargs["audience"] == BACKEND_URL


def test_unset_audience_skips_that_check_but_still_validates_others(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    """Backward-compat: if backend_base_url is empty, audience isn't pinned,
    but signature + issuer + caller + email_verified still run."""
    patch_settings.return_value = _prod_settings(backend_base_url="")
    patch_verify.return_value = _valid_claims()
    _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    _, kwargs = patch_verify.call_args
    assert kwargs["audience"] is None


def test_unset_caller_sa_skips_that_check_but_still_validates_others(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings(blocking_function_service_account="")
    patch_verify.return_value = _valid_claims(email="anyone@example.com")
    # Should pass because caller SA is unconfigured.
    _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))


def test_unset_caller_sa_does_not_bypass_issuer_check(
    patch_settings: MagicMock, patch_verify: MagicMock
) -> None:
    patch_settings.return_value = _prod_settings(blocking_function_service_account="")
    patch_verify.return_value = _valid_claims(iss="https://evil.example.com")
    with pytest.raises(HTTPException) as exc:
        _verify_blocking_function_token(_make_request({"authorization": "Bearer tok"}))
    assert exc.value.status_code == 403
