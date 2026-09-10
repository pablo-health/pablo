# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's card-processing credentials come from.

A card charge needs a Stripe secret key, the publishable key the browser posts
card details with, and sometimes the id of the Stripe account the objects
should belong to. They are resolved together because they have to agree.

:class:`SettingsPaymentCredentialProvider` is the default: the configured
``STRIPE_SECRET_KEY`` and ``STRIPE_PATIENT_BILLING_PUBLISHABLE_KEY``, charging
directly on the account they belong to. A deployment that needs something else
(one key acting for several Stripe accounts, per-practice credentials from a
secret store) implements the protocol and installs it at startup with
:func:`register_payment_credential_provider`, the same registry shape as
``app.notes.registry``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..settings import get_settings


@dataclass(frozen=True, slots=True)
class PaymentCredentials:
    """What one Stripe call needs for a practice.

    ``account_id`` is sent as ``Stripe-Account`` and is ``None`` when the key
    is the account's own. ``publishable_key`` is not a secret, but it lives
    here so whatever resolves the secret key also resolves the publishable key
    that must match it: a live secret key with a test publishable key collects
    cards that can never be charged, and neither side reports anything wrong.
    """

    secret_key: str
    account_id: str | None = None
    publishable_key: str = ""


class PaymentCredentialProvider(Protocol):
    """Resolves a practice to the credentials its card charges are made with."""

    def credentials_for_practice(self, practice_id: str | None) -> PaymentCredentials | None:
        """The credentials for ``practice_id``, or ``None``.

        ``None`` means this practice cannot take card payments right now;
        callers turn it into a 503, never a 403. ``practice_id`` is ``None``
        on a deployment with no practice registry.
        """
        ...


class SettingsPaymentCredentialProvider:
    """Default provider: the deployment's own configured key, charged directly.

    No ``Stripe-Account`` header is sent. The key is read per call so a
    redeployed key needs no code change.
    """

    def credentials_for_practice(
        self,
        practice_id: str | None,  # noqa: ARG002 — argument documents the protocol's shape
    ) -> PaymentCredentials | None:
        settings = get_settings()
        secret_key = settings.stripe_secret_key.get_secret_value()
        if not secret_key:
            return None
        return PaymentCredentials(
            secret_key=secret_key,
            publishable_key=settings.stripe_patient_billing_publishable_key,
        )


@dataclass
class _ProviderRegistry:
    provider: PaymentCredentialProvider | None = None


_registry = _ProviderRegistry()
_default_provider = SettingsPaymentCredentialProvider()


def register_payment_credential_provider(provider: PaymentCredentialProvider | None) -> None:
    """Install the process-global provider, or pass ``None`` to restore the default.

    Call once during startup, before the first request.
    """
    _registry.provider = provider


def get_payment_credential_provider() -> PaymentCredentialProvider:
    """The registered provider, or :class:`SettingsPaymentCredentialProvider`."""
    return _registry.provider or _default_provider
