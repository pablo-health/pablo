# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What has happened to one filed claim, in the order it happened.

A claim leaves the practice, the clearinghouse or the payer acknowledges it,
and eventually the payer says what it paid. Those three arrive as separate
documents — an 837, a 277CA, an 835 — and reassembling them into "what
happened to *this* claim" is most of the work of knowing whether a practice
got paid.

The clearinghouse can do that reassembly, keyed to the claim it was given.
This is that answer: entries in order, each one either a submission, an
acknowledgement, or what a payer reported paying.

Amounts are cents, and may be negative — a payer reversing an earlier payment
reports it as a claim payment with a negative amount, and treating that as a
positive credit would silently overstate what a practice has collected.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

#: What a payer's report of a claim actually means for the money.
#:
#: ``estimate`` is the trap: a predetermination is the payer pricing a claim
#: it has not adjudicated, so it carries amounts that look exactly like a
#: payment and must never be posted as one.
PaymentDisposition = Literal["paid", "denied", "reversed", "forwarded", "estimate"]

AcknowledgmentOutcome = Literal["accepted", "rejected", "pending", "unknown"]


class TimelineSubmission(BaseModel):
    """The claim leaving the practice — the first entry, and any resubmission."""

    id: str
    patient_control_number: str
    charged_cents: int
    processed_at: datetime


class TimelineAcknowledgment(BaseModel):
    """A 277CA: somebody has the claim, and either kept it or sent it back.

    ``reported_by`` matters for what to do next. A clearinghouse rejection is
    ours to fix and resend; a payer rejection may be a coverage question the
    practice has to answer.
    """

    id: str
    outcome: AcknowledgmentOutcome
    reported_by: str
    source_name: str
    processed_at: datetime


class TimelinePayment(BaseModel):
    """What a payer reported for this claim in an electronic remittance.

    Claim level, not service line: this is one ``CLP`` loop. Posting to
    individual lines needs the remittance itself.

    ``trace_number`` is the check number or EFT trace, and is what ties this
    to money that actually arrived in the practice's account.
    """

    id: str
    disposition: PaymentDisposition
    charged_cents: int
    paid_cents: int
    patient_responsibility_cents: int | None = None
    trace_number: str | None = None
    processed_at: datetime

    @property
    def is_money(self) -> bool:
        """Whether this entry represents money moving, in either direction."""
        return self.disposition in ("paid", "reversed")


class ClaimTimeline(BaseModel):
    """One claim's history, oldest first."""

    submissions: list[TimelineSubmission] = []
    acknowledgments: list[TimelineAcknowledgment] = []
    payments: list[TimelinePayment] = []
    next_page_token: str | None = None

    @property
    def paid_cents(self) -> int:
        """What the payer has reported paying, reversals subtracted.

        Denials, forwards and predeterminations contribute nothing: none of
        them is money.
        """
        return sum(payment.paid_cents for payment in self.payments if payment.is_money)

    @property
    def patient_responsibility_cents(self) -> int:
        """What the client owes, according to whoever adjudicated last.

        Not a sum. Each payer reports only the responsibility it assigned
        itself — a secondary never restates the primary's — so every entry
        here is a complete statement of the balance after that payer
        finished, and adding them together bills one session twice. A
        secondary that pays off the primary's coinsurance assigns nothing,
        and the answer is nothing.

        Every adjudication counts, including a denial. A service the plan
        does not cover is denied and the client owes the whole charge;
        looking only at entries that moved money would report that they owe
        nothing.
        """
        adjudications = [
            payment
            for payment in self.payments
            if payment.disposition in ("paid", "denied", "reversed")
        ]
        if not adjudications:
            return 0
        latest = max(adjudications, key=lambda payment: payment.processed_at)
        return latest.patient_responsibility_cents or 0
