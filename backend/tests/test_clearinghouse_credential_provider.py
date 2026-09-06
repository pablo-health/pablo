# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the clearinghouse-credential configuration point.

Mirrors ``test_payment_credential_provider.py``: the default provider reads
the deployment's own configured key and infers test-vs-production from it,
reports "not configured" rather than half-working when unset, and a
deployment that registers its own provider gets asked instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.claims.credentials import (
    ClearinghouseCredentials,
    SettingsClearinghouseCredentialProvider,
    get_clearinghouse_credential_provider,
    mode_for_key,
    register_clearinghouse_credential_provider,
)

# Placeholder-shaped on purpose: the secret scan allowlists literal
# placeholders, and only the test_ prefix matters to the provider.
_TEST_KEY = "test_placeholder"
_PRODUCTION_KEY = "live_placeholder"
_CUSTOM_KEY = "provider-supplied-key-for-tests"


class _Settings:
    def __init__(self, key: str | None) -> None:
        self.clearinghouse_api_key = key


@pytest.fixture(autouse=True)
def _restore_default() -> Any:
    yield
    register_clearinghouse_credential_provider(None)


class TestCredentialsRepr:
    def test_the_key_is_not_in_the_repr(self) -> None:
        credentials = ClearinghouseCredentials(api_key=_TEST_KEY, mode="test")

        assert _TEST_KEY not in repr(credentials)
        assert "mode='test'" in repr(credentials)


class TestModeForKey:
    def test_a_test_prefixed_key_is_test_mode(self) -> None:
        assert mode_for_key(_TEST_KEY) == "test"

    def test_any_other_key_is_production_mode(self) -> None:
        assert mode_for_key(_PRODUCTION_KEY) == "production"
        assert mode_for_key("key_test_placeholder") == "production"


class TestDefaultProvider:
    def test_the_default_is_used_when_nothing_is_registered(self) -> None:
        assert isinstance(
            get_clearinghouse_credential_provider(), SettingsClearinghouseCredentialProvider
        )

    def test_a_test_key_is_reported_as_test_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.claims.credentials.get_settings", lambda: _Settings(_TEST_KEY))

        credentials = SettingsClearinghouseCredentialProvider().get("practice-1")

        assert credentials == ClearinghouseCredentials(api_key=_TEST_KEY, mode="test")

    def test_a_non_test_key_is_reported_as_production_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.claims.credentials.get_settings", lambda: _Settings(_PRODUCTION_KEY)
        )

        credentials = SettingsClearinghouseCredentialProvider().get("practice-1")

        assert credentials == ClearinghouseCredentials(api_key=_PRODUCTION_KEY, mode="production")

    def test_an_unset_key_reports_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.claims.credentials.get_settings", lambda: _Settings(None))

        assert SettingsClearinghouseCredentialProvider().get(None) is None


class TestRegistration:
    def test_a_registered_provider_replaces_the_default(self) -> None:
        class _Custom:
            def get(self, practice_id: str | None) -> ClearinghouseCredentials | None:
                return ClearinghouseCredentials(api_key=f"{_CUSTOM_KEY}-{practice_id}", mode="test")

        register_clearinghouse_credential_provider(_Custom())

        credentials = get_clearinghouse_credential_provider().get("p1")

        assert credentials == ClearinghouseCredentials(api_key=f"{_CUSTOM_KEY}-p1", mode="test")

    def test_registering_none_restores_the_default(self) -> None:
        class _Custom:
            def get(self, practice_id: str | None) -> ClearinghouseCredentials | None:
                return None

        register_clearinghouse_credential_provider(_Custom())
        register_clearinghouse_credential_provider(None)

        assert isinstance(
            get_clearinghouse_credential_provider(), SettingsClearinghouseCredentialProvider
        )
