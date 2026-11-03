# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET /api/users/me/status.

Covers the practice_id population path added for multi-tenant deployments
so the dashboard can gate practice-scoped UI (e.g. audio-retention
settings) on `practiceId` being present in the response.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest
from app.payments.provider import PaymentCredentials, register_payment_credential_provider

_SECRET_KEY = "sk_test_secret"
_PUBLISHABLE_KEY = "pk_test_public"


class TestUserStatusPracticeId:
    def test_includes_practice_id_when_email_maps_to_practice(self, client: Any) -> None:
        with (
            patch("app.settings.get_settings") as mock_settings,
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-abc", "practice_abc"),
            ),
        ):
            mock_settings.return_value.is_saas = False

            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert response.json()["practice_id"] == "practice-abc"

    def test_omits_practice_id_when_email_has_no_mapping(self, client: Any) -> None:
        with (
            patch("app.settings.get_settings") as mock_settings,
            patch("app.auth.service._resolve_practice_from_email", return_value=None),
        ):
            mock_settings.return_value.is_saas = False

            response = client.get("/api/users/me/status")

        assert response.status_code == 200
        assert "practice_id" not in response.json()


class _StubProvider:
    """Answers with fixed credentials, and records which practice was asked about."""

    def __init__(self, credentials: PaymentCredentials | None) -> None:
        self.credentials = credentials
        self.asked_for: list[str | None] = []

    def credentials_for_practice(self, practice_id: str | None) -> PaymentCredentials | None:
        self.asked_for.append(practice_id)
        return self.credentials


@pytest.fixture
def restore_default_provider() -> Iterator[None]:
    yield
    register_payment_credential_provider(None)


@pytest.mark.usefixtures("restore_default_provider")
class TestUserStatusCardPayments:
    """The chart reads this once to decide whether to ask for a card at all."""

    def _status(self, client: Any, provider: _StubProvider) -> dict[str, Any]:
        register_payment_credential_provider(provider)
        with (
            patch("app.settings.get_settings") as mock_settings,
            patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-abc", "practice_abc"),
            ),
        ):
            mock_settings.return_value.is_saas = False
            response = client.get("/api/users/me/status")
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    def test_a_practice_with_no_credentials_does_not_take_cards(self, client: Any) -> None:
        assert self._status(client, _StubProvider(None))["card_payments_configured"] is False

    def test_a_practice_with_credentials_takes_cards(self, client: Any) -> None:
        provider = _StubProvider(
            PaymentCredentials(secret_key=_SECRET_KEY, publishable_key=_PUBLISHABLE_KEY)
        )

        body = self._status(client, provider)

        assert body["card_payments_configured"] is True
        assert provider.asked_for == ["practice-abc"]
        # Only the yes/no leaves the server, never a key.
        assert _SECRET_KEY not in str(body)
        assert _PUBLISHABLE_KEY not in str(body)
