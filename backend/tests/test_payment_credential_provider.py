# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the payment-credential configuration point.

Two things worth pinning. The default provider is what a bare deployment gets —
its own configured secret key, charging directly, with no account named — and
it reports "not configured" rather than half-working when the key is unset. And
a deployment that registers its own provider gets asked instead, so the routes
never need to know which case they are in.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.payments.provider import (
    PaymentCredentials,
    SettingsPaymentCredentialProvider,
    get_payment_credential_provider,
    register_payment_credential_provider,
)
from pydantic import SecretStr

# Deliberately not shaped like a real key. Nothing here parses the value — it
# only has to round-trip through the provider — and a fixture that imitated a
# credential would be indistinguishable from a leaked one to a secret scanner,
# which is exactly the judgement we want the scanner making on a public repo.
_CONFIGURED_KEY = "configured-key-for-tests"
_CUSTOM_KEY = "provider-supplied-key-for-tests"
_PUBLISHABLE_KEY = "configured-publishable-key-for-tests"


class _Settings:
    def __init__(self, key: str, publishable_key: str = "") -> None:
        self.stripe_secret_key = SecretStr(key)
        self.stripe_publishable_key = publishable_key


@pytest.fixture(autouse=True)
def _restore_default() -> Any:
    yield
    register_payment_credential_provider(None)


class TestDefaultProvider:
    def test_the_default_is_used_when_nothing_is_registered(self) -> None:
        assert isinstance(get_payment_credential_provider(), SettingsPaymentCredentialProvider)

    def test_it_charges_directly_on_the_configured_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.payments.provider.get_settings",
            lambda: _Settings(_CONFIGURED_KEY, _PUBLISHABLE_KEY),
        )

        credentials = SettingsPaymentCredentialProvider().credentials_for_practice("practice-1")

        # The publishable key rides along with the secret key it has to
        # match, rather than being configured somewhere else and hoped
        # to agree.
        assert credentials == PaymentCredentials(
            secret_key=_CONFIGURED_KEY, publishable_key=_PUBLISHABLE_KEY
        )
        # No account named: the key is the account's own, so nothing is charged
        # on behalf of anybody else.
        assert credentials is not None
        assert credentials.account_id is None

    def test_an_unset_key_reports_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Better than a half-working charge path: the routes turn this into a
        503 saying card payments are not set up."""
        monkeypatch.setattr("app.payments.provider.get_settings", lambda: _Settings(""))

        assert SettingsPaymentCredentialProvider().credentials_for_practice(None) is None


class TestRegistration:
    def test_a_registered_provider_replaces_the_default(self) -> None:
        class _Custom:
            def credentials_for_practice(
                self, practice_id: str | None
            ) -> PaymentCredentials | None:
                return PaymentCredentials(secret_key=_CUSTOM_KEY, account_id=f"acct_{practice_id}")

        register_payment_credential_provider(_Custom())

        credentials = get_payment_credential_provider().credentials_for_practice("p1")

        assert credentials == PaymentCredentials(secret_key=_CUSTOM_KEY, account_id="acct_p1")

    def test_registering_none_restores_the_default(self) -> None:
        class _Custom:
            def credentials_for_practice(
                self,
                practice_id: str | None,
            ) -> PaymentCredentials | None:
                return None

        register_payment_credential_provider(_Custom())
        register_payment_credential_provider(None)

        assert isinstance(get_payment_credential_provider(), SettingsPaymentCredentialProvider)
