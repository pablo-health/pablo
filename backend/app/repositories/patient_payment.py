# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Card-on-file and charge-ledger data access.

The write methods here **commit**, which is unusual for a repository in this
codebase and is the point: the ledger row must be durable before any money can
move, so an attempt that dies mid-flight still leaves a row to reconcile. Each
charge method names the commit boundary it owns (:meth:`commit`,
:meth:`record_payment_intent`, :meth:`close_charge`), and the route reads as
the sequence of things that are true at each step.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from ..models.payments import CardOnFile, PatientCharge


class PatientPaymentRepository(ABC):
    """Reads and writes for one practice's card-on-file and charge ledger."""

    @abstractmethod
    def get_card_on_file(self, patient_id: str) -> CardOnFile | None:
        """The client's card row, or ``None`` if setup was never started."""

    @abstractmethod
    def start_card_setup(
        self, *, patient_id: str, stripe_customer_id: str, user_id: str
    ) -> CardOnFile:
        """Create the card row for a newly-minted processor customer, and commit.

        The customer already exists at the processor; losing its id would
        strand it.
        """

    @abstractmethod
    def complete_card_setup(  # noqa: PLR0913 — the display triple plus its keys
        self,
        *,
        patient_id: str,
        stripe_payment_method_id: str,
        brand: str | None,
        last4: str | None,
        exp_month: int | None,
        exp_year: int | None,
        user_id: str,
    ) -> CardOnFile | None:
        """Attach the confirmed payment method and its display fields.

        Returns ``None`` when there is no card row to complete.
        """

    @abstractmethod
    def stage_charge(  # noqa: PLR0913 — the ledger row's own shape
        self,
        *,
        patient_id: str,
        appointment_id: str | None,
        amount_cents: int,
        currency: str,
        user_id: str,
        kind: str = "session",
        claim_id: str | None = None,
    ) -> PatientCharge:
        """Write a ``pending`` ledger row and flush it, without committing.

        Flushing assigns the id the caller's audit entry needs; :meth:`commit`
        then makes the row and the audit entry durable together. A copay uses
        this same path with ``kind="copay"``.
        """

    @abstractmethod
    def record_settlement(self, charge_id: str, *, settled_by_charge_id: str) -> None:
        """Mark an owed row as settled by the charge that collected it.

        The charge succeeds on its own row; the row it pays off is a different
        one, and without this the same dollar reads as both owed and collected.
        """

    @abstractmethod
    def add_ledger_row(  # noqa: PLR0913 — the ledger row's own shape
        self,
        *,
        patient_id: str,
        kind: str,
        amount_cents: int,
        currency: str,
        user_id: str,
        appointment_id: str | None = None,
        claim_id: str | None = None,
        write_off_reason: str | None = None,
        note: str | None = None,
    ) -> PatientCharge:
        """Record a ledger row that no card charge produced, and commit it.

        Remittance postings, adjustments, write-offs and credits have no
        payment intent behind them, so the row is written ``succeeded`` in its
        final form.
        """

    @abstractmethod
    def commit(self) -> None:
        """Make everything staged on this request durable.

        Called after :meth:`stage_charge` and its audit entry, before the
        processor is contacted.
        """

    @abstractmethod
    def record_payment_intent(self, charge_id: str, payment_intent_id: str) -> None:
        """Stamp the processor's PaymentIntent id onto the row and commit.

        Called after the intent is created and before it is confirmed, so every
        intent that could move money is one already written down.
        """

    @abstractmethod
    def close_charge(
        self, charge_id: str, *, status: str, status_detail: str | None
    ) -> PatientCharge:
        """Record the outcome on the ledger row and commit."""

    @abstractmethod
    def list_charges(self, patient_id: str) -> list[PatientCharge]:
        """This client's ledger, newest first."""

    @abstractmethod
    def list_all_charges(self) -> list[PatientCharge]:
        """Every ledger row the caller can see, oldest first; backs the balances view.

        No client filter and no clinician argument: the session is scoped to
        one practice's schema and the ``has_patient_access`` row policy hides
        clients this clinician holds no grant on. A predicate here would be a
        drifting second copy of that rule.
        """

    @abstractmethod
    def iter_ledger_for_period(self, *, start: datetime, end: datetime) -> Iterator[PatientCharge]:
        """The practice's ledger for a half-open period, oldest first.

        ``start`` included, ``end`` excluded, with the id as tiebreaker so the
        same period renders identically twice. Yields because the export
        behind it runs a year at a time.
        """

    @abstractmethod
    def succeeded_charge_kinds(self, appointment_ids: list[str]) -> dict[str, set[str]]:
        """The kinds of succeeded charge on each appointment, keyed by id.

        Appointments with no succeeded charge are absent. Kinds rather than a
        boolean because a session charge settles the visit while a copay is a
        part payment; the unbilled queue makes that judgement, not this query.
        """
