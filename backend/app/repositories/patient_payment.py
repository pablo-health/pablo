# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Card-on-file and charge-ledger data access.

The write methods here **commit**, which is unusual for a repository in this
codebase and is the point of the design rather than an oversight. The charge
flow's whole safety property is that the ledger row is durable before any money
can move: an attempt that dies mid-flight has to leave a row saying it happened
so a human can reconcile it against the processor. A row that only reaches the
database when the request finishes would be rolled back by exactly the failures
it exists to record.

So the charge methods each name the commit boundary they own — :meth:`commit`
(the row exists, together with the audit entry describing the same act),
:meth:`record_payment_intent` (we know what could move money),
:meth:`close_charge` (the outcome) — and the route reads as the sequence of
things that are true at each step.
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
        """Create the card row for a newly-minted processor customer.

        Committed: the customer already exists at the processor by the time
        this is called, and losing our record of its id would strand it.
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

        Flushing is what assigns the row its id, which the caller needs in
        order to write the audit entry naming this charge. :meth:`commit` then
        makes both durable together — the audit entry and the ledger row
        describe one act, and neither should survive without the other.

        ``kind`` defaults to ``session`` so the existing charge-for-a-visit
        caller is unchanged. A copay taken at the door goes through this same
        path with ``kind="copay"`` — it is an ordinary card charge, and it
        becomes collected money only when the processor says ``succeeded``.
        """

    @abstractmethod
    def record_settlement(self, charge_id: str, *, settled_by_charge_id: str) -> None:
        """Mark an owed row as settled by the charge that collected it.

        Separate from :meth:`close_charge` because the two rows are different:
        the charge succeeds on its own row, and the row it pays off is another
        one entirely (a ``patient_resp`` the remittance wrote, say). Without
        this the same dollar reads as both owed and collected.
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

        Remittance posting, contractual adjustments, write-offs and credits
        are all money facts with no payment intent behind them: nothing is
        called, so there is no pending state to reconcile and the row is
        written in its final form. ``status`` is ``succeeded`` because the
        fact is settled the moment it is recorded — a write-off does not
        later fail.
        """

    @abstractmethod
    def commit(self) -> None:
        """Make everything staged on this request durable.

        Called immediately after :meth:`stage_charge` and its audit entry, and
        before the processor is contacted at all.
        """

    @abstractmethod
    def record_payment_intent(self, charge_id: str, payment_intent_id: str) -> None:
        """Stamp the processor's PaymentIntent id onto the row and commit.

        Called after the intent is created and *before* it is confirmed, so
        every intent that could move money is one already written down.
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
    def iter_ledger_for_period(self, *, start: datetime, end: datetime) -> Iterator[PatientCharge]:
        """The practice's whole ledger for a period, one row at a time.

        Half-open — ``start`` included, ``end`` excluded — because the caller
        turns a pair of calendar dates into moments, and the day after the
        window's last day is the only boundary that puts a row recorded at
        23:59:59 inside the window exactly once. Oldest first, with the id as
        the tiebreaker, so the same period renders the same bytes twice.

        It yields rather than returning a list because the export this backs
        runs over a year at a time, and every client's rows at once is not a
        thing to hold in memory.
        """

    @abstractmethod
    def succeeded_appointment_ids(self, appointment_ids: list[str]) -> set[str]:
        """Which of these appointments have at least one succeeded charge.

        Backs the unbilled queue: an appointment absent from the result has
        no succeeded charge, whether that's no attempt at all or a decline
        (``failed``) — either way it belongs in the queue.
        """
