# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Self-pay card payments: a practice charging its own clients' cards.

Three pieces:

* :mod:`app.payments.provider` — the per-deployment configuration point that
  answers "which Stripe credentials does this practice charge with".
* :mod:`app.payments.stripe_api` — the thin Stripe REST client the routes use.
* ``app.routes.patient_payments`` / ``app.routes.payment_webhooks`` — the API
  and the outcome receiver.

Nothing here holds money on a practice's behalf and nothing here ever sees a
card number: the browser posts the card straight to Stripe and this package
stores an opaque payment-method id plus the brand/last4/expiry the UI renders.
"""

from __future__ import annotations

from .provider import (
    PaymentCredentialProvider,
    PaymentCredentials,
    SettingsPaymentCredentialProvider,
    get_payment_credential_provider,
    register_payment_credential_provider,
)

__all__ = [
    "PaymentCredentialProvider",
    "PaymentCredentials",
    "SettingsPaymentCredentialProvider",
    "get_payment_credential_provider",
    "register_payment_credential_provider",
]
