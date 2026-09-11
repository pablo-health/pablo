# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for the practice's financial report.

Mirrors ``app.payments.reporting`` the way ``BalanceResponse`` mirrors
``app.payments.balance.BalanceSummary`` — the domain layer stays plain
dataclasses, and this is the typed shape the route hands back. Ids and
amounts only: no client, no payer contact detail, nothing clinical.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class AgingBucketResponse(BaseModel):
    label: str
    count: int
    cents: int


class AgingReportResponse(BaseModel):
    buckets: list[AgingBucketResponse]


class PayerMixEntryResponse(BaseModel):
    payer_id: str
    payer_name: str
    billed_cents: int
    collected_cents: int


class PayerMixReportResponse(BaseModel):
    entries: list[PayerMixEntryResponse]


class CollectionsRateResponse(BaseModel):
    """The rate is ``collected / (billed - contractual_adjustment -
    write_off)``, left to the caller: cents divide exactly and percentages
    do not, so the API stays exact and rounds nowhere."""

    billed_cents: int
    collected_cents: int
    contractual_adjustment_cents: int
    write_off_cents: int


class PayerLagResponse(BaseModel):
    payer_id: str
    payer_name: str
    claim_count: int
    median_days: float
    p90_days: float


class ClaimPaymentLagReportResponse(BaseModel):
    by_payer: list[PayerLagResponse]


class BillingReportResponse(BaseModel):
    """The four sections together, for the window (or as-of date) given."""

    from_date: date
    to_date: date
    aging: AgingReportResponse
    payer_mix: PayerMixReportResponse
    collections_rate: CollectionsRateResponse
    claim_payment_lag: ClaimPaymentLagReportResponse
