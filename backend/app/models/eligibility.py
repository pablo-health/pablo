# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an eligibility check found, in the shape the chart renders.

A 271 is a payer document with dozens of benefit lines; the chart needs an
answer to five questions about one kind of visit: is the plan active, what
does the client pay at the door, how much deductible is left, are visits
capped or gated on an authorization, and does somebody other than the payer
on the card administer behavioral benefits. This is that answer, built by
``app.claims.eligibility`` from the stored response.

None of it is a payment guarantee. A 271 says what the payer knew when it
was asked, for the benefit it was asked about; the copy that renders it
says "plan active as of", never "covered".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: ``active`` and ``inactive`` are the payer's answer; ``unknown`` is a 271
#: that answered without saying either way for this benefit; ``error`` is an
#: AAA rejection (the payer never answered the coverage question).
EligibilityStatus = Literal["active", "inactive", "unknown", "error"]

EligibilityTrigger = Literal["intake", "save", "manual", "scheduled"]


class CarveoutAdministrator(BaseModel):
    """Somebody other than the payer on the card administers behavioral benefits.

    ``payer_id`` is the administrator's electronic payer id when the 271
    carried one — the id a claim for this benefit is filed under.
    """

    name: str
    payer_id: str | None = None


class VisitLimit(BaseModel):
    """A cap on visits. Either side may be missing: a payer often reports
    only what remains, or only the plan-year total."""

    remaining: int | None = None
    total: int | None = None


class AaaError(BaseModel):
    """One AAA rejection, with the vendor's plain-language resolution text."""

    code: str
    description: str
    followup_action: str
    resolution: str | None = None


class EligibilitySummary(BaseModel):
    """The normalized reading of a client's most recent 271."""

    status: EligibilityStatus
    checked_at: datetime
    #: The payer's own name and id as the 271 carries them.
    payer_name: str | None = None
    plan_name: str | None = None
    #: ``YYYY-MM-DD`` when the payer reported a plan start.
    plan_begin: str | None = None
    copay_cents: int | None = None
    coinsurance_pct: float | None = None
    deductible_remaining_cents: int | None = None
    visit_limit: VisitLimit | None = None
    requires_authorization: bool | None = None
    carveout_administrator: CarveoutAdministrator | None = None
    aaa_errors: list[AaaError] = []
