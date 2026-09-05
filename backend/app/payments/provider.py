# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where a practice's card-processing credentials come from.

Collecting and charging a card needs three things: a Stripe secret key, the
publishable key the browser posts card details with, and — for some
deployments — the id of the Stripe account the objects should belong to when
that is not simply the account the key itself belongs to. All three are
deployment configuration, so they are read through a small provider rather than
baked into the routes, and they are resolved together because they have to
agree with one another.

:class:`SettingsPaymentCredentialProvider` is the default and is what a bare
deployment gets: the keys configured as ``STRIPE_SECRET_KEY`` and
``STRIPE_PATIENT_BILLING_PUBLISHABLE_KEY``, charging directly on the account
they belong to, with no ``account_id``. A deployment that
needs something else — one key authorised to act for several Stripe accounts,
credentials fetched from a secret store per practice, a key that rotates on its
own schedule — implements the protocol and installs it at startup with
:func:`register_payment_credential_provider`.

The registry is the same shape the rest of the codebase uses for this kind of
configuration point (see ``app.jobs.hard_purge_retention_stub`` and
``app.notes.registry``): a protocol, one implementation shipped here, and a
process-global setter called once during startup rather than per request.
Registration is a statement about the deployment, not about a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..settings import get_settings


@dataclass(frozen=True, slots=True)
class PaymentCredentials:
    """What one Stripe call needs to be made for a practice.

    ``secret_key`` authenticates the call.

    ``account_id`` is the Stripe account the created objects belong to, sent as
    Stripe's ``Stripe-Account`` header, and is ``None`` in the default
    configuration — the key is the account's own key, so there is nobody else
    to act for and the header is omitted entirely. It exists because a
    deployment may hold one key that is authorised to act for more than one
    Stripe account, in which case every call has to say which.

    ``publishable_key`` is the browser's half of the same pair. It is not a
    secret — it is meant to reach the client, which is the only place it does
    anything — and it lives here rather than being read separately so that
    whatever resolves the secret key also resolves the publishable key that has
    to match it. Split across two sources they drift silently: a live secret
    key with a test publishable key collects cards that can never be charged,
    and neither side reports anything wrong.
    """

    secret_key: str
    account_id: str | None = None
    publishable_key: str = ""


class PaymentCredentialProvider(Protocol):
    """Resolves a practice to the credentials its card charges are made with."""

    def credentials_for_practice(self, practice_id: str | None) -> PaymentCredentials | None:
        """Return the credentials for ``practice_id``, or ``None``.

        ``None`` means this practice cannot take card payments right now —
        nothing is configured, or setup is unfinished. Callers turn that into
        a 503, never a 403: the caller is not forbidden, the precondition is
        simply missing.

        ``practice_id`` is ``None`` on a deployment that runs a single practice
        and therefore has no practice registry to key on.
        """
        ...


class SettingsPaymentCredentialProvider:
    """Default provider: this deployment's own configured Stripe secret key.

    Charges directly on the account the key belongs to — no ``account_id``, so
    no ``Stripe-Account`` header is ever sent. ``practice_id`` is accepted and
    ignored: one deployment, one key, and reading it per call rather than at
    import time means a redeployed key takes effect without a code change.
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

    Call once during startup, before the first request. Tests use the ``None``
    form to put the default back.
    """
    _registry.provider = provider


def get_payment_credential_provider() -> PaymentCredentialProvider:
    """The registered provider, or :class:`SettingsPaymentCredentialProvider`."""
    return _registry.provider or _default_provider
