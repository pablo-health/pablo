# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Firebase Admin SDK initialization: WIF vs ADC vs emulator.

``conftest.py`` autouse-patches ``initialize_firebase_app`` for the rest of
the suite so no test accidentally reaches the network; these tests need the
real function, so they shadow that fixture with a no-op for this module only.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.firebase_init import initialize_firebase_app
from app.settings import Settings

WIF_AUDIENCE = (
    "//iam.googleapis.com/projects/123456789/locations/global/"
    "workloadIdentityPools/pablo-pool/providers/pablo-provider"
)
WIF_IMPERSONATION_URL = (
    "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
    "wif-runner@my-project.iam.gserviceaccount.com:generateAccessToken"
)


@pytest.fixture(autouse=True)
def mock_firebase_init() -> None:
    """Shadow the repo-wide autouse mock so these tests hit the real code."""
    return None


@pytest.fixture(autouse=True)
def _clear_cache() -> Any:
    initialize_firebase_app.cache_clear()
    yield
    initialize_firebase_app.cache_clear()


def _settings(**overrides: Any) -> Settings:
    return Settings(
        environment="development",
        firebase_project_id=overrides.pop("firebase_project_id", "my-project"),
        firebase_workload_identity=overrides.pop("firebase_workload_identity", False),
        firebase_wif_audience=overrides.pop("firebase_wif_audience", ""),
        firebase_wif_sa_impersonation_url=overrides.pop("firebase_wif_sa_impersonation_url", ""),
        **overrides,
    )


class TestInitializeFirebaseApp:
    def test_emulator_takes_priority_and_needs_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "localhost:9099")
        with (
            patch("app.auth.firebase_init.get_settings", return_value=_settings()),
            patch("app.auth.firebase_init.firebase_admin.initialize_app") as mock_init,
        ):
            mock_init.return_value = MagicMock()
            initialize_firebase_app()

        assert mock_init.call_count == 1
        args, kwargs = mock_init.call_args
        assert args == ()
        assert "options" in kwargs

    def test_workload_identity_env_selects_wif_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
        settings = _settings(
            firebase_workload_identity=True,
            firebase_wif_audience=WIF_AUDIENCE,
            firebase_wif_sa_impersonation_url=WIF_IMPERSONATION_URL,
        )

        with (
            patch("app.auth.firebase_init.get_settings", return_value=settings),
            patch("app.auth.firebase_init.firebase_admin.initialize_app") as mock_init,
        ):
            mock_init.return_value = MagicMock()
            initialize_firebase_app()

        assert mock_init.call_count == 1
        cred_arg, options = mock_init.call_args[0]
        assert cred_arg.service_account_email == ("wif-runner@my-project.iam.gserviceaccount.com")
        assert options["serviceAccountId"] == "wif-runner@my-project.iam.gserviceaccount.com"

    def test_default_env_selects_application_default_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
        settings = _settings(firebase_workload_identity=False)

        with (
            patch("app.auth.firebase_init.get_settings", return_value=settings),
            patch("app.auth.firebase_init.credentials.ApplicationDefault") as mock_adc,
            patch("app.auth.firebase_init.firebase_admin.initialize_app") as mock_init,
        ):
            mock_adc.return_value = "adc-cred-sentinel"
            mock_init.return_value = MagicMock()
            initialize_firebase_app()

        assert mock_adc.call_count == 1
        cred_arg, _options = mock_init.call_args[0]
        assert cred_arg == "adc-cred-sentinel"

    def test_result_is_cached_across_calls(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
        settings = _settings(firebase_workload_identity=False)

        with (
            patch("app.auth.firebase_init.get_settings", return_value=settings),
            patch("app.auth.firebase_init.credentials.ApplicationDefault"),
            patch("app.auth.firebase_init.firebase_admin.initialize_app") as mock_init,
        ):
            mock_init.return_value = MagicMock()
            first = initialize_firebase_app()
            second = initialize_firebase_app()

        assert first is second
        assert mock_init.call_count == 1
