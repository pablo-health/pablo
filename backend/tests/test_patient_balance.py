# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the balance arithmetic (``app.payments.balance``).

The function is pure, so these are about the RULES rather than any wiring:
which kinds are owed, which count as collected, which reduce the balance
without anyone paying, and which are recorded but owed by nobody. Each test
names the rule it pins, because the cost of getting one wrong is a client
billed for money they do not owe.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models.payments import PatientCharge
from app.payments.balance import patient_balance

_PATIENT = "11111111-1111-4111-8111-111111111111"
_VISIT = "33333333-3333-4333-8333-333333333333"
_OTHER_VISIT = "44444444-4444-4444-8444-444444444444"


def _charge(
    *,
    kind: str = "session",
    status: str = "succeeded",
    amount_cents: int = 10_000,
    appointment_id: str | None = _VISIT,
    settled_by_charge_id: str | None = None,
    write_off_reason: str | None = None,
    charge_id: str = "charge-1",
) -> PatientCharge:
    return PatientCharge(
        id=charge_id,
        patient_id=_PATIENT,
        appointment_id=appointment_id,
        kind=kind,
        write_off_reason=write_off_reason,
        settled_by_charge_id=settled_by_charge_id,
        amount_cents=amount_cents,
        currency="usd",
        status=status,
        created_by_user_id="user-1",
        created_at=datetime.now(UTC),
    )


def test_empty_ledger_is_a_zero_balance() -> None:
    summary = patient_balance([])

    assert summary.balance_cents == 0
    assert summary.by_visit == ()


def test_an_unpaid_session_charge_is_owed() -> None:
    summary = patient_balance([_charge(kind="session", status="pending")])

    assert summary.owed_cents == 10_000
    assert summary.collected_cents == 0
    assert summary.balance_cents == 10_000


def test_a_succeeded_session_charge_is_collected_and_not_owed() -> None:
    """The same row cannot be both: money that arrived is not money owed."""
    summary = patient_balance([_charge(kind="session", status="succeeded")])

    assert summary.owed_cents == 0
    assert summary.collected_cents == 10_000
    assert summary.balance_cents == -10_000


def test_a_failed_session_charge_is_still_owed() -> None:
    """A declined card does not settle a debt."""
    summary = patient_balance([_charge(kind="session", status="failed")])

    assert summary.owed_cents == 10_000
    assert summary.collected_cents == 0


def test_a_refunded_charge_collected_nothing() -> None:
    summary = patient_balance([_charge(kind="session", status="refunded")])

    assert summary.collected_cents == 0
    assert summary.owed_cents == 10_000


def test_a_disputed_charge_still_counts_as_collected() -> None:
    """The practice is holding the money until the bank decides otherwise."""
    summary = patient_balance([_charge(kind="session", status="disputed")])

    assert summary.collected_cents == 10_000


def test_a_lost_dispute_collected_nothing() -> None:
    summary = patient_balance([_charge(kind="session", status="dispute_lost")])

    assert summary.collected_cents == 0
    assert summary.owed_cents == 10_000


def test_a_succeeded_copay_is_collected_but_never_owed() -> None:
    """A copay row is written when it is taken, so it is never a debt."""
    summary = patient_balance([_charge(kind="copay", status="succeeded", amount_cents=2_500)])

    assert summary.collected_cents == 2_500
    assert summary.owed_cents == 0
    assert summary.balance_cents == -2_500


def test_a_pending_copay_is_neither_owed_nor_collected() -> None:
    """A card charge in flight has not arrived and was never a debt."""
    summary = patient_balance([_charge(kind="copay", status="pending", amount_cents=2_500)])

    assert summary.collected_cents == 0
    assert summary.owed_cents == 0
    assert summary.balance_cents == 0


def test_patient_responsibility_is_owed() -> None:
    summary = patient_balance([_charge(kind="patient_resp", status="pending", amount_cents=4_000)])

    assert summary.owed_cents == 4_000
    assert summary.balance_cents == 4_000


def test_a_settled_owed_row_stops_being_owed() -> None:
    """The dollar arrived on the settling charge; it must not be owed twice."""
    rows = [
        _charge(
            kind="patient_resp",
            status="pending",
            amount_cents=4_000,
            settled_by_charge_id="charge-2",
            charge_id="charge-1",
        ),
        _charge(kind="copay", status="succeeded", amount_cents=4_000, charge_id="charge-2"),
    ]

    summary = patient_balance(rows)

    assert summary.owed_cents == 0
    assert summary.collected_cents == 4_000
    assert summary.balance_cents == -4_000


def test_a_contractual_adjustment_is_owed_by_nobody() -> None:
    """It explains where the practice's rate went; it is not a debt or a payment."""
    rows = [
        _charge(kind="patient_resp", status="pending", amount_cents=4_000, charge_id="charge-1"),
        _charge(
            kind="contractual_adjustment",
            status="succeeded",
            amount_cents=6_000,
            charge_id="charge-2",
        ),
    ]

    summary = patient_balance(rows)

    assert summary.adjusted_cents == 6_000
    assert summary.owed_cents == 4_000
    assert summary.collected_cents == 0
    assert summary.balance_cents == 4_000


def test_a_write_off_reduces_the_balance_without_anyone_paying() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, charge_id="charge-1"),
        _charge(
            kind="write_off",
            status="succeeded",
            amount_cents=10_000,
            write_off_reason="hardship",
            charge_id="charge-2",
        ),
    ]

    summary = patient_balance(rows)

    assert summary.owed_cents == 10_000
    assert summary.written_off_cents == 10_000
    assert summary.collected_cents == 0
    assert summary.balance_cents == 0


def test_an_over_collected_copay_leaves_a_credit_the_practice_owes() -> None:
    """A negative balance is a refund owed, and must not be clamped to zero."""
    rows = [
        _charge(kind="patient_resp", status="pending", amount_cents=2_000, charge_id="charge-1"),
        _charge(kind="copay", status="succeeded", amount_cents=5_000, charge_id="charge-2"),
    ]

    summary = patient_balance(rows)

    assert summary.owed_cents == 2_000
    assert summary.collected_cents == 5_000
    assert summary.balance_cents == -3_000


def test_an_explicit_credit_reduces_the_balance() -> None:
    rows = [
        _charge(kind="session", status="pending", amount_cents=10_000, charge_id="charge-1"),
        _charge(kind="credit", status="succeeded", amount_cents=2_500, charge_id="charge-2"),
    ]

    summary = patient_balance(rows)

    assert summary.credited_cents == 2_500
    assert summary.balance_cents == 7_500


def test_rows_are_grouped_by_visit_and_the_lines_sum_to_the_total() -> None:
    rows = [
        _charge(
            kind="session",
            status="pending",
            amount_cents=10_000,
            appointment_id=_VISIT,
            charge_id="charge-1",
        ),
        _charge(
            kind="session",
            status="pending",
            amount_cents=8_000,
            appointment_id=_OTHER_VISIT,
            charge_id="charge-2",
        ),
    ]

    summary = patient_balance(rows)

    assert len(summary.by_visit) == 2
    assert sum(visit.balance_cents for visit in summary.by_visit) == summary.balance_cents
    assert summary.balance_cents == 18_000


def test_rows_with_no_visit_become_a_single_trailing_line() -> None:
    """A statement whose lines do not sum to its total is worse than an "other" row."""
    rows = [
        _charge(
            kind="session",
            status="pending",
            amount_cents=10_000,
            appointment_id=_VISIT,
            charge_id="charge-1",
        ),
        _charge(
            kind="session",
            status="pending",
            amount_cents=5_000,
            appointment_id=None,
            charge_id="charge-2",
        ),
        _charge(
            kind="credit",
            status="succeeded",
            amount_cents=1_000,
            appointment_id=None,
            charge_id="charge-3",
        ),
    ]

    summary = patient_balance(rows)

    assert [visit.appointment_id for visit in summary.by_visit] == [_VISIT, None]
    assert summary.by_visit[-1].owed_cents == 5_000
    assert summary.by_visit[-1].credited_cents == 1_000
    assert sum(visit.balance_cents for visit in summary.by_visit) == summary.balance_cents
