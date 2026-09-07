# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the statement build and render (``app.payments.statement``).

What these pin down:

* one line per visit, carrying the visit's date and service from the diary
  and its money from the ledger and the claim filed for it;
* a self-pay visit is billed at its session charge, because no claim exists
  to say otherwise, and its insurance-paid column is zero;
* an insured visit is billed at the claim's figure and shows what the payer
  paid, so the client can see where the practice's rate went;
* every line's arithmetic closes — charge less insurance, adjustment and
  what the client paid is what the line says they owe — and the totals are
  the sum of the lines;
* the balance the document prints is the one
  :func:`app.payments.balance.patient_balance` computes, so the statement and
  the chart header can never disagree;
* rows that belong to no visit still appear, on one trailing line;
* lines are chronological, whatever order the ledger came back in;
* the same inputs give byte-identical PDFs, and nothing clinical is on the
  page.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.models.payments import PatientCharge
from app.payments.balance import patient_balance
from app.payments.statement import (
    PracticeBlock,
    build_statement,
    render_statement_pdf,
)
from app.scheduling_engine.models.appointment import Appointment

from tests.claims_fixtures import (
    APPOINTMENT_ID,
    BUILT_AT,
    CLAIM_ID,
    PATIENT_ID,
    USER_ID,
    claim,
    line,
)

_TZ = ZoneInfo("America/New_York")
_GENERATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_SECOND_APPOINTMENT_ID = "77777777-7777-4777-8777-777777777777"
_CLIENT_NAME = "John Anon"

_PRACTICE = PracticeBlock(
    name="Sample Counseling",
    address_line1="123 Some St",
    address_line2=None,
    city="Atlanta",
    state="GA",
    postal_code="30301",
    phone="5553334444",
)


def _appointment(
    appointment_id: str = APPOINTMENT_ID,
    *,
    start_at: datetime = datetime(2026, 9, 1, 14, 0, tzinfo=UTC),
    session_type: str = "Individual therapy",
) -> Appointment:
    return Appointment(
        id=appointment_id,
        user_id=USER_ID,
        patient_id=PATIENT_ID,
        title="Session",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=50),
        duration_minutes=50,
        status="completed",
        session_type=session_type,
    )


def _charge(**overrides: Any) -> PatientCharge:
    fields: dict[str, Any] = {
        "id": "charge-1",
        "patient_id": PATIENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "kind": "session",
        "amount_cents": 15_000,
        "currency": "usd",
        "status": "succeeded",
        "created_by_user_id": USER_ID,
        "created_at": BUILT_AT,
    }
    fields.update(overrides)
    return PatientCharge(**fields)


def _build(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "client_name": _CLIENT_NAME,
        "charges": [_charge()],
        "claims": [],
        "appointments": [_appointment()],
        "practice": _PRACTICE,
        "timezone": _TZ,
        "generated_at": _GENERATED_AT,
    }
    fields.update(overrides)
    return build_statement(**fields)


# ---------------------------------------------------------------------------
# What a line says
# ---------------------------------------------------------------------------


class TestLines:
    def test_a_self_pay_visit_is_billed_at_its_session_charge(self) -> None:
        """No claim was filed, so the ledger is the only thing that says what
        the visit cost — and no payer paid any of it."""
        statement = _build()

        assert len(statement.lines) == 1
        visit = statement.lines[0]
        assert visit.appointment_id == APPOINTMENT_ID
        assert visit.charged_cents == 15_000
        assert visit.insurance_paid_cents == 0
        assert visit.paid_cents == 15_000
        assert visit.owed_cents == 0

    def test_an_unpaid_self_pay_visit_is_owed_in_full(self) -> None:
        statement = _build(charges=[_charge(status="pending")])

        visit = statement.lines[0]
        assert visit.charged_cents == 15_000
        assert visit.paid_cents == 0
        assert visit.owed_cents == 15_000

    def test_a_declined_charge_still_leaves_the_visit_owed(self) -> None:
        """A card that said no did not settle anything."""
        statement = _build(charges=[_charge(status="failed", status_detail="card_declined")])

        assert statement.lines[0].owed_cents == 15_000

    def test_the_visit_carries_its_date_and_service_from_the_diary(self) -> None:
        statement = _build()

        assert statement.lines[0].service_date == date(2026, 9, 1)
        assert statement.lines[0].service == "Individual therapy"

    def test_the_service_date_is_the_practices_own_day_not_utcs(self) -> None:
        """A seven-in-the-evening session is on the day the client remembers."""
        statement = _build(
            appointments=[_appointment(start_at=datetime(2026, 9, 2, 1, 0, tzinfo=UTC))]
        )

        assert statement.lines[0].service_date == date(2026, 9, 1)

    def test_a_visit_missing_from_the_diary_keeps_its_money(self) -> None:
        """The appointment is gone; what the client owes for it is not."""
        statement = _build(appointments=[])

        assert statement.lines[0].service_date is None
        assert statement.lines[0].service == ""
        assert statement.lines[0].charged_cents == 15_000


class TestInsuredVisits:
    def test_the_claim_says_what_was_billed_and_what_the_payer_paid(self) -> None:
        rows = [
            _charge(id="c-1", kind="contractual_adjustment", amount_cents=6_000),
            _charge(id="c-2", kind="patient_resp", amount_cents=2_000, status="pending"),
        ]
        statement = _build(
            charges=rows,
            claims=[claim(lines=[line(charge_cents=16_000, paid_cents=8_000)])],
        )

        visit = statement.lines[0]
        assert visit.charged_cents == 16_000
        assert visit.insurance_paid_cents == 8_000
        assert visit.adjusted_cents == 6_000
        assert visit.paid_cents == 0
        assert visit.owed_cents == 2_000

    def test_the_line_closes(self) -> None:
        """Charge less insurance, adjustment and what the client paid IS what
        the line says is owed. A statement whose row does not add up invites
        exactly the phone call it was meant to prevent."""
        rows = [
            _charge(id="c-1", kind="contractual_adjustment", amount_cents=6_000),
            _charge(id="c-2", kind="patient_resp", amount_cents=2_000, status="pending"),
            _charge(id="c-3", kind="payment", amount_cents=2_000, status="succeeded"),
        ]
        statement = _build(
            charges=rows,
            claims=[claim(lines=[line(charge_cents=16_000, paid_cents=8_000)])],
        )

        visit = statement.lines[0]
        assert (
            visit.charged_cents
            - visit.insurance_paid_cents
            - visit.adjusted_cents
            - visit.paid_cents
            == visit.owed_cents
        )
        assert visit.owed_cents == 0

    def test_a_claim_beats_the_session_charge_as_the_billed_figure(self) -> None:
        """Both exist for the visit; the claim is what was actually billed."""
        statement = _build(
            charges=[_charge(amount_cents=15_000)],
            claims=[claim(lines=[line(charge_cents=16_000)])],
        )

        assert statement.lines[0].charged_cents == 16_000

    def test_a_voided_claim_does_not_bill_the_visit(self) -> None:
        """The same rule the superbill applies: a void is not a standing claim."""
        statement = _build(
            charges=[_charge(amount_cents=15_000)],
            claims=[claim(frequency_code="8", lines=[line(charge_cents=16_000)])],
        )

        assert statement.lines[0].charged_cents == 15_000


class TestTotals:
    def test_the_balance_is_the_one_the_ledger_computes(self) -> None:
        """The statement does not do its own arithmetic; it prints the
        module's, so the header and the document cannot disagree."""
        rows = [
            _charge(id="c-1", kind="patient_resp", amount_cents=6_200, status="pending"),
            _charge(id="c-2", kind="session", amount_cents=15_000, status="succeeded"),
        ]
        statement = _build(charges=rows)

        assert statement.balance_cents == patient_balance(rows).balance_cents
        assert statement.balance_cents == 6_200

    def test_totals_are_the_sum_of_the_lines(self) -> None:
        rows = [
            _charge(id="c-1", amount_cents=15_000, status="pending"),
            _charge(
                id="c-2",
                appointment_id=_SECOND_APPOINTMENT_ID,
                amount_cents=16_000,
                status="succeeded",
            ),
        ]
        statement = _build(
            charges=rows,
            appointments=[
                _appointment(),
                _appointment(
                    _SECOND_APPOINTMENT_ID, start_at=datetime(2026, 9, 8, 14, 0, tzinfo=UTC)
                ),
            ],
        )

        assert len(statement.lines) == 2
        assert statement.total_charged_cents == 31_000
        assert statement.total_paid_cents == 16_000
        assert statement.balance_cents == sum(line.owed_cents for line in statement.lines)

    def test_a_credit_shows_as_a_negative_balance(self) -> None:
        """A refund the practice owes must not be clamped to zero."""
        statement = _build(
            charges=[_charge(kind="credit", amount_cents=1_000, appointment_id=None)],
            appointments=[],
        )

        assert statement.balance_cents == -1_000


class TestOrdering:
    def test_lines_are_chronological_whatever_order_the_ledger_gave(self) -> None:
        later = _appointment(
            _SECOND_APPOINTMENT_ID, start_at=datetime(2026, 9, 8, 14, 0, tzinfo=UTC)
        )
        rows = [
            _charge(id="c-1", appointment_id=_SECOND_APPOINTMENT_ID, status="pending"),
            _charge(id="c-2", appointment_id=APPOINTMENT_ID, status="pending"),
        ]
        statement = _build(charges=rows, appointments=[later, _appointment()])

        assert [line.service_date for line in statement.lines] == [
            date(2026, 9, 1),
            date(2026, 9, 8),
        ]

    def test_rows_belonging_to_no_visit_become_one_trailing_line(self) -> None:
        rows = [
            _charge(id="c-1", status="pending"),
            _charge(id="c-2", appointment_id=None, amount_cents=5_000, status="pending"),
        ]
        statement = _build(charges=rows)

        assert statement.lines[-1].appointment_id is None
        assert statement.lines[-1].service == "Other charges"
        assert statement.lines[-1].owed_cents == 5_000


class TestRendering:
    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        assert render_statement_pdf(_build()) == render_statement_pdf(_build())

    def test_a_different_ledger_gives_different_bytes(self) -> None:
        assert render_statement_pdf(_build()) != render_statement_pdf(
            _build(charges=[_charge(amount_cents=20_000)])
        )

    def test_it_is_a_pdf(self) -> None:
        assert render_statement_pdf(_build()).startswith(b"%PDF")

    def test_the_ledger_rows_it_totalled_are_named_for_the_audit_entry(self) -> None:
        statement = _build(charges=[_charge(id="c-2"), _charge(id="c-1", status="pending")])

        assert statement.charge_ids == ("c-1", "c-2")


class TestWhatIsNotOnThePage:
    def test_no_diagnosis_code_reaches_the_statement(self) -> None:
        """The claim carries diagnoses; a document about money does not. A
        client's diagnoses have no bearing on what they owe, and this one gets
        left on desks and handed across waiting rooms."""
        statement = _build(
            charges=[_charge(kind="patient_resp", amount_cents=2_000, status="pending")],
            claims=[claim(diagnosis_codes=["F41.1"], lines=[line(charge_cents=16_000)])],
        )

        assert b"F41.1" not in render_statement_pdf(statement)

    def test_no_member_id_reaches_the_statement(self) -> None:
        """The subscriber's member id is on the claim; the plan is not what
        this document is about."""
        statement = _build(
            charges=[_charge(kind="patient_resp", amount_cents=2_000, status="pending")],
            claims=[claim(lines=[line(claim_id=CLAIM_ID, charge_cents=16_000)])],
        )

        assert b"123456789" not in render_statement_pdf(statement)
