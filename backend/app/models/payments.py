# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain and API models for self-pay card payments.

Two domain models — the card a practice has on file for a client, and one row
of the charge ledger — plus the request/response shapes the API speaks.

Neither domain model carries a card number, because nothing in the system ever
holds one: the browser posts the card straight to Stripe and hands the backend
an opaque payment-method id. ``brand``/``last4``/``exp_*`` are display fields,
and they are the only card-shaped values that exist anywhere below this line.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

#: Largest single charge the API will attempt, in minor units. Not a processor
#: limit — a blast-radius cap on a fat-fingered amount (cents typed into a
#: dollars box, or a UI regression sending 100x). Far above any plausible
#: single self-pay session and far below "career-ending mistake". Rejected with
#: a 422, never silently clamped.
MAX_CHARGE_CENTS = 1_000_000


class CardOnFile(BaseModel):
    """The card a practice keeps on file for one client.

    ``stripe_payment_method_id`` is ``None`` between "setup started" and "the
    browser confirmed the card" — that row is not chargeable, which is what
    :attr:`chargeable` reports.
    """

    id: str
    patient_id: str
    stripe_customer_id: str
    stripe_payment_method_id: str | None = None
    card_brand: str | None = None
    card_last4: str | None = None
    card_exp_month: int | None = None
    card_exp_year: int | None = None

    @property
    def chargeable(self) -> bool:
        return bool(self.stripe_payment_method_id)


class PatientCharge(BaseModel):
    """One row of the charge ledger — one attempt to charge a client's card."""

    id: str
    patient_id: str
    appointment_id: str | None = None
    amount_cents: int
    currency: str
    status: str
    status_detail: str | None = None
    stripe_payment_intent_id: str | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime | None = None


class CardSetupResponse(BaseModel):
    """What the browser needs in order to collect a card.

    ``stripe_account_id`` is present only when the deployment's credential
    provider named an account; Stripe.js has to be initialised with it as
    ``stripeAccount`` in that case, and must not be in the default one.
    """

    client_secret: str
    stripe_account_id: str | None = None


class CardSetupConfirmation(BaseModel):
    """The SetupIntent the browser just confirmed.

    Only the id. What card actually got attached is then read back from
    Stripe — the browser is not trusted for the display fields, because a
    caller that could write them could make the stored card read as one card
    while a charge went to another.
    """

    setup_intent_id: str = Field(min_length=1, max_length=255)


class CardOnFileResponse(BaseModel):
    """The card on file, as the UI renders it. Display fields only."""

    brand: str | None = None
    last4: str | None = None
    exp_month: int | None = None
    exp_year: int | None = None
    chargeable: bool = False


class CreateChargeRequest(BaseModel):
    """A one-click charge.

    ``amount_cents`` is optional: left out, the amount is resolved from the
    client's own rate, falling back to the default fee of the appointment's
    type. Sending it overrides that for this one charge (a partial payment, a
    late-cancellation fee).

    Currency is not a parameter — the deployment charges in one currency and a
    caller cannot pick another.
    """

    amount_cents: int | None = Field(default=None, gt=0, le=MAX_CHARGE_CENTS)
    appointment_id: str | None = None


class ChargeResponse(BaseModel):
    """One ledger row, as the practice sees it.

    No Stripe customer or payment-method id and no card data: the ledger is
    amounts, statuses and reasons.
    """

    id: str
    amount_cents: int
    currency: str
    status: str
    status_detail: str | None = None
    appointment_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
