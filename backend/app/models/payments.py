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
from typing import Literal

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
    # What the row is — see ``app.db.models.CHARGE_KINDS``. Defaulted so a
    # ledger row read from before the column existed, or written by a caller
    # that predates it, is the full-rate session charge it always was.
    kind: str = "session"
    claim_id: str | None = None
    write_off_reason: str | None = None
    note: str | None = None
    settled_by_charge_id: str | None = None
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

    All three fields configure the same Stripe.js instance, which is why they
    are returned together rather than assembled from separate sources: the
    publishable key and the account have to match the secret key the resulting
    card will eventually be charged with, and a mismatch produces a card that
    saves and then never charges.

    ``publishable_key`` is public by design — it is the key the browser posts
    card details with, and it does nothing else.

    ``stripe_account_id`` is present only when the deployment's credential
    provider named an account; Stripe.js has to be initialised with it as
    ``stripeAccount`` in that case, and must not be in the default one.
    """

    client_secret: str
    publishable_key: str
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

    ``amount_cents`` is optional: left out, the amount is resolved on the
    server from what ``kind`` says this charge is. Sending it overrides that
    for this one charge (a partial payment, a late-cancellation fee, or a
    copay nobody has on file).

    ``kind`` is the two things a card is charged for. ``session`` resolves to
    the client's own rate, falling back to the appointment type's default
    fee; ``copay`` resolves to what a covered client pays at the door and
    records a row a later remittance can net out. The remaining ledger kinds
    are not charges at all — a write-off or a contractual adjustment moves no
    money and cannot be raised here.

    Currency is not a parameter — the deployment charges in one currency and a
    caller cannot pick another.
    """

    amount_cents: int | None = Field(default=None, gt=0, le=MAX_CHARGE_CENTS)
    appointment_id: str | None = None
    kind: Literal["session", "copay"] = "session"


class CreateWriteOffRequest(BaseModel):
    """A practice-initiated write-off: money it has decided not to collect.

    ``reason`` is checked at the route against ``app.db.models.WRITE_OFF_REASONS``
    — the same set the CHECK constraint enforces — rather than encoded as a
    ``Literal`` here, so there is one list to update rather than two that can
    drift apart. ``courtesy`` and ``small_balance`` are further gated by
    practice policy; ``hardship`` and ``error`` are not.
    """

    amount_cents: int = Field(gt=0, le=MAX_CHARGE_CENTS)
    reason: str = Field(min_length=1, max_length=24)
    note: str | None = Field(default=None, max_length=2000)


class ChargeAmountResponse(BaseModel):
    """What a charge sent without an explicit amount would come to.

    Exists so the clinician sees the figure *before* committing to it. The
    charge route resolves the same value from the same helper; asking for it
    first is a read, and reading it changes nothing.

    ``amount_cents`` is ``None`` when neither the client nor the appointment
    type sets a rate. That is not zero and must not be rendered as free — it
    means the UI has to ask for an amount, exactly as the charge route would
    refuse without one.
    """

    amount_cents: int | None = None
    currency: str


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
    kind: str = "session"
    claim_id: str | None = None
    write_off_reason: str | None = None
    note: str | None = None
    settled_by_charge_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class VisitBalanceResponse(BaseModel):
    """One visit's line of the balance. ``appointment_id`` is ``None`` for the
    rows that hang off no visit, collapsed into a single trailing line."""

    appointment_id: str | None = None
    owed_cents: int
    collected_cents: int
    written_off_cents: int
    adjusted_cents: int
    credited_cents: int
    balance_cents: int


class BalanceResponse(BaseModel):
    """What a client owes, and the arithmetic that produced it.

    ``balance_cents`` is positive when the client owes the practice and
    negative when the practice owes the client — a credit is not clamped to
    zero, because a refund the practice owes is exactly the thing a clamped
    balance would hide.
    """

    owed_cents: int
    collected_cents: int
    written_off_cents: int
    adjusted_cents: int
    credited_cents: int
    balance_cents: int
    by_visit: list[VisitBalanceResponse]


class ClientBalanceItem(BaseModel):
    """One client on the practice-wide balances list.

    ``outstanding_since`` is when the money behind the balance first went on
    the ledger, which is what the list is ordered by: the oldest balance is
    the one that most needs a conversation, not the largest.
    """

    patient_id: str
    patient_name: str
    balance_cents: int
    currency: str
    outstanding_since: datetime


class BalancesResponse(BaseModel):
    """Every client carrying a balance, oldest first."""

    items: list[ClientBalanceItem]
