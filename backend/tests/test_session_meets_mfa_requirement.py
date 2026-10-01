# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``/me/status`` reports the same second-factor decision ``require_mfa`` makes.

The dashboard decides whether to send a clinician to an MFA screen from
``session_mfa_satisfied``. If that disagreed with ``require_mfa`` -- an
allow-listed E2E account outside production passes the API but was reported
as unsatisfied -- the screen would block a session the API admits. One
function now answers both; these tests pin that it does, for every setting
combination that changes the answer.
"""

from __future__ import annotations

from itertools import product
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
from app.auth.providers import VerifiedIdentity
from app.auth.service import require_mfa, session_meets_mfa_requirement
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

E2E_EMAIL = "e2e@pablo.health"


def _identity(*, email: str, mfa: bool, verified: bool = True) -> VerifiedIdentity:
    return VerifiedIdentity(
        provider="firebase",
        subject_id="uid-1",
        email=email,
        mfa_satisfied=mfa,
        claims={"uid": "uid-1", "email": email, "email_verified": verified},
    )


def _settings(**overrides: Any) -> MagicMock:
    settings = MagicMock()
    settings.require_mfa = True
    settings.is_development = False
    settings.is_prod_project = False
    settings.e2e_test_emails = {E2E_EMAIL}
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


@pytest.mark.parametrize(
    ("settings", "identity", "expected"),
    [
        pytest.param(_settings(), _identity(email="c@x.com", mfa=True), True, id="second-factor"),
        pytest.param(
            _settings(), _identity(email="c@x.com", mfa=False), False, id="first-factor-only"
        ),
        pytest.param(
            _settings(require_mfa=False),
            _identity(email="c@x.com", mfa=False),
            True,
            id="not-required",
        ),
        pytest.param(
            _settings(is_development=True),
            _identity(email="c@x.com", mfa=False),
            True,
            id="dev-mode",
        ),
        pytest.param(
            _settings(), _identity(email=E2E_EMAIL, mfa=False), True, id="e2e-outside-prod"
        ),
        pytest.param(
            _settings(is_prod_project=True),
            _identity(email=E2E_EMAIL, mfa=False),
            False,
            id="e2e-in-prod",
        ),
        pytest.param(
            _settings(),
            _identity(email=E2E_EMAIL, mfa=False, verified=False),
            False,
            id="e2e-unverified-email",
        ),
    ],
)
def test_the_decision(settings: MagicMock, identity: VerifiedIdentity, expected: bool) -> None:
    with patch("app.auth.service.get_settings", return_value=settings):
        assert session_meets_mfa_requirement(identity) is expected


@pytest.mark.parametrize(
    ("require", "dev", "prod", "email", "mfa"),
    list(
        product([True, False], [True, False], [True, False], ["c@x.com", E2E_EMAIL], [True, False])
    ),
)
def test_status_and_authorization_never_disagree(
    require: bool, dev: bool, prod: bool, email: str, mfa: bool
) -> None:
    """Whatever the settings, require_mfa admits a token exactly when the
    status would report it satisfied."""
    identity = _identity(email=email, mfa=mfa)
    settings = _settings(require_mfa=require, is_development=dev, is_prod_project=prod)
    credentials = Mock(spec=HTTPAuthorizationCredentials)
    credentials.credentials = "token"

    with (
        patch("app.auth.service.get_settings", return_value=settings),
        patch("app.auth.service._verify_request_identity", return_value=identity),
    ):
        reported = session_meets_mfa_requirement(identity)
        try:
            require_mfa(MagicMock(), credentials)
            admitted = True
        except HTTPException:
            admitted = False

    assert reported is admitted
