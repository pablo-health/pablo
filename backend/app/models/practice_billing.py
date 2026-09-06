# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's billing identity — the "who is filing this claim" a
clearinghouse and payer need on a claim header."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BillingProfileResponse(BaseModel):
    """The practice's billing identity.

    ``tax_id_last4`` is the only trace of the tax id that ever leaves the
    server — the full value is encrypted at rest and never returned once
    saved, matching the "masked after save" settings-page behavior.
    """

    legal_name: str | None = None
    tax_id_last4: str | None = None
    tax_id_type: Literal["ein", "ssn"] | None = None
    billing_npi: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None
    #: The practice's general inbox, where payers and the clearinghouse
    #: write about enrollments. Never an individual clinician's address.
    contact_email: str | None = None
    #: The clearinghouse's id for the practice's provider record, set once
    #: the profile is complete enough to register. Read-only.
    clearinghouse_provider_id: str | None = None
    #: Run an eligibility check on its own when coverage lands at intake or is
    #: saved on the chart. Default on; off leaves the manual re-verify button.
    eligibility_auto_check: bool = True


class UpdateBillingProfileRequest(BaseModel):
    """Partial update. An omitted field keeps its current value.

    ``tax_id`` is the one write-only field: the raw EIN or SSN, accepted
    here and never echoed back — the response only ever carries
    ``tax_id_last4``.
    """

    legal_name: str | None = Field(None, min_length=1, max_length=255)
    tax_id: str | None = Field(None, min_length=4, max_length=20)
    tax_id_type: Literal["ein", "ssn"] | None = None
    billing_npi: str | None = Field(None, pattern=r"^\d{10}$")
    address_line1: str | None = Field(None, max_length=255)
    address_line2: str | None = Field(None, max_length=255)
    city: str | None = Field(None, max_length=100)
    state: str | None = Field(None, max_length=2)
    postal_code: str | None = Field(None, max_length=10)
    phone: str | None = Field(None, max_length=50)
    contact_email: str | None = Field(None, max_length=255)
    eligibility_auto_check: bool | None = None
