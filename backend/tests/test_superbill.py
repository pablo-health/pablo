# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the superbill build and render (``app.claims.superbill``).

What these pin down:

* the document is a render of the client's visits — a visit a claim was
  built for prints from its claim lines, and a visit with no claim prints
  from the appointment, priced the way the statement prices it — with the
  date, code, fee and what the ledger says was paid, and its totals are the
  sum of those lines;
* the diagnosis is printed only when the client's superbill includes it, and
  then a service without one refuses the document; otherwise the column is
  not on the page and a missing diagnosis is not a gap;
* narrowing the period drops lines and the totals move with them;
* voids and corrected parents are left out, and a visit with two standing
  claims appears once;
* only a succeeded charge counts as paid, and a visit's payment is spread
  over its lines in line order;
* anything missing refuses the whole document, naming the field: the
  rendering NPI, a line's service code, its fee, and (when printed) its
  diagnosis;
* the same inputs give byte-identical PDFs, and nothing on the path loads a
  model client.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.claims.superbill import (
    SuperbillRefusedError,
    build_superbill,
    current_claims,
    render_superbill_pdf,
)
from app.models.payments import PatientCharge
from app.people_term import people_words
from app.repositories.clinician_profile import ClinicianProfile
from app.scheduling_engine.models.appointment import Appointment
from app.services import structured_llm_gateway, vertex_client

from tests.claims_fixtures import (
    APPOINTMENT_ID,
    BUILT_AT,
    PATIENT_ID,
    USER_ID,
    billing_snapshot,
    claim,
    line,
    person,
)

_TZ = ZoneInfo("America/New_York")
_GENERATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
_CLIENTS = people_words("clients")
_SECOND_APPOINTMENT_ID = "77777777-7777-4777-8777-777777777777"
_SECOND_CLAIM_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
_TAX_ID = "123456789"

_PROFILE = ClinicianProfile(
    user_id=USER_ID,
    practice_id="practice-1",
    npi_number="1999999984",
    license_number="LCSW-4321",
    license_state="GA",
)


def _license_for(user_id: str) -> ClinicianProfile | None:
    return _PROFILE if user_id == USER_ID else None


def _second_claim(**overrides: Any) -> Any:
    """A second visit a week later, on its own claim."""
    fields: dict[str, Any] = {
        "id": _SECOND_CLAIM_ID,
        "control_number": "88659892",
        "created_at": BUILT_AT + timedelta(days=7),
        "updated_at": BUILT_AT + timedelta(days=7),
        "diagnosis_codes": ["F41.1", "F33.1"],
        "lines": [
            line(
                id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                claim_id=_SECOND_CLAIM_ID,
                appointment_id=_SECOND_APPOINTMENT_ID,
                line_control_number="886598921",
                service_date=date(2026, 9, 8),
                charge_cents=16000,
                dx_pointers=[1, 2],
            )
        ],
    }
    fields.update(overrides)
    return claim(**fields)


def _charge(**overrides: Any) -> PatientCharge:
    fields: dict[str, Any] = {
        "id": "charge-1",
        "patient_id": PATIENT_ID,
        "appointment_id": APPOINTMENT_ID,
        "amount_cents": 15000,
        "currency": "usd",
        "status": "succeeded",
        "created_by_user_id": USER_ID,
        "created_at": BUILT_AT,
    }
    fields.update(overrides)
    return PatientCharge(**fields)


def _appointment(**overrides: Any) -> Appointment:
    fields: dict[str, Any] = {
        "id": APPOINTMENT_ID,
        "user_id": USER_ID,
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": datetime(2026, 9, 1, 19, 0, tzinfo=UTC),
        "end_at": datetime(2026, 9, 1, 20, 0, tzinfo=UTC),
        "duration_minutes": 53,
        "status": "confirmed",
        "session_type": "individual",
    }
    fields.update(overrides)
    return Appointment(**fields)


def _build(**overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "period_start": date(2026, 9, 1),
        "period_end": date(2026, 9, 30),
        "patient": person(),
        "billing": billing_snapshot(),
        "claims": [claim(), _second_claim()],
        "charges": [_charge()],
        "appointments": [],
        "rate_for": lambda _appointment: None,
        "includes_diagnosis": True,
        "timezone": _TZ,
        "tax_id": _TAX_ID,
        "license_for": _license_for,
        "generated_at": _GENERATED_AT,
        "people": _CLIENTS,
    }
    fields.update(overrides)
    return build_superbill(**fields)


def _refusal(**overrides: Any) -> list[Any]:
    with pytest.raises(SuperbillRefusedError) as excinfo:
        _build(**overrides)
    return excinfo.value.findings


# ---------------------------------------------------------------------------
# Lines and totals
# ---------------------------------------------------------------------------


class TestLines:
    def test_one_line_per_claim_line_in_the_period(self) -> None:
        superbill = _build()
        assert [
            (entry.service_date, entry.cpt, entry.charge_cents) for entry in superbill.lines
        ] == [
            (date(2026, 9, 1), "90837", 15000),
            (date(2026, 9, 8), "90837", 16000),
        ]

    def test_line_carries_the_diagnoses_its_pointers_name(self) -> None:
        superbill = _build()
        assert superbill.lines[0].diagnosis_codes == ("F41.1",)
        assert superbill.lines[1].diagnosis_codes == ("F41.1", "F33.1")

    def test_totals_are_the_sum_of_the_lines(self) -> None:
        superbill = _build()
        assert superbill.total_charge_cents == 31000
        assert superbill.total_paid_cents == 15000

    def test_narrowing_the_period_moves_the_totals_with_the_lines(self) -> None:
        superbill = _build(period_end=date(2026, 9, 5))
        assert [entry.service_date for entry in superbill.lines] == [date(2026, 9, 1)]
        assert superbill.total_charge_cents == 15000
        assert superbill.total_paid_cents == 15000

    def test_fee_is_the_claim_line_charge_not_a_second_source(self) -> None:
        superbill = _build(claims=[claim(lines=[line(charge_cents=12345)])])
        assert superbill.lines[0].charge_cents == 12345

    def test_ids_name_the_claims_lines_and_charges_rendered(self) -> None:
        superbill = _build()
        assert superbill.claim_ids == (claim().id, _SECOND_CLAIM_ID)
        assert superbill.line_ids == (claim().lines[0].id, _second_claim().lines[0].id)
        assert superbill.charge_ids == ("charge-1",)


class TestWhichClaims:
    def test_void_and_its_parent_are_left_out(self) -> None:
        parent = claim(state="submitted")
        void = claim(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            control_number="88659893",
            frequency_code="8",
            parent_claim_id=parent.id,
            created_at=BUILT_AT + timedelta(days=1),
        )
        assert current_claims([parent, void]) == []

    def test_correction_replaces_its_parent(self) -> None:
        parent = claim(state="submitted")
        corrected = claim(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            control_number="88659893",
            frequency_code="7",
            parent_claim_id=parent.id,
            created_at=BUILT_AT + timedelta(days=1),
            lines=[line(claim_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", charge_cents=17000)],
        )
        assert [c.id for c in current_claims([parent, corrected])] == [corrected.id]
        superbill = _build(claims=[parent, corrected])
        assert superbill.lines[0].charge_cents == 17000

    def test_two_standing_claims_for_one_visit_show_it_once_from_the_newest(self) -> None:
        older = claim()
        newer = claim(
            id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
            control_number="88659893",
            created_at=BUILT_AT + timedelta(hours=1),
            lines=[line(claim_id="eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", charge_cents=15500)],
        )
        superbill = _build(claims=[newer, older])
        assert [(entry.claim_id, entry.charge_cents) for entry in superbill.lines] == [
            (newer.id, 15500)
        ]


class TestPayments:
    @pytest.mark.parametrize(
        "status", ["pending", "failed", "refunded", "disputed", "dispute_lost"]
    )
    def test_only_a_succeeded_charge_is_paid(self, status: str) -> None:
        superbill = _build(charges=[_charge(status=status)])
        assert superbill.total_paid_cents == 0
        assert superbill.charge_ids == ()

    def test_a_charge_with_no_visit_is_not_on_the_document(self) -> None:
        superbill = _build(charges=[_charge(appointment_id=None)])
        assert superbill.total_paid_cents == 0

    def test_a_visit_payment_is_spread_over_its_lines_in_order(self) -> None:
        with_add_on = claim(
            lines=[
                line(),
                line(
                    id="ffffffff-ffff-4fff-8fff-ffffffffffff",
                    line_number=2,
                    line_control_number="886598912",
                    cpt="90833",
                    charge_cents=6000,
                ),
            ]
        )
        superbill = _build(claims=[with_add_on], charges=[_charge(amount_cents=18000)])
        assert [(entry.cpt, entry.paid_cents) for entry in superbill.lines] == [
            ("90837", 15000),
            ("90833", 3000),
        ]
        assert superbill.total_paid_cents == 18000

    def test_an_overpayment_stays_on_the_visit_last_line(self) -> None:
        superbill = _build(claims=[claim()], charges=[_charge(amount_cents=20000)])
        assert superbill.lines[0].paid_cents == 20000
        assert superbill.total_paid_cents == 20000


# ---------------------------------------------------------------------------
# Refusing
# ---------------------------------------------------------------------------


class TestRefusal:
    def test_missing_rendering_npi_refuses_and_names_the_field(self) -> None:
        findings = _refusal(billing=billing_snapshot(npi=None))
        assert [(f.code, f.field) for f in findings] == [
            ("missing_field", "rendering_provider.npi"),
        ]

    def test_missing_tax_id_refuses(self) -> None:
        findings = _refusal(tax_id=None)
        assert [f.field for f in findings] == ["billing_provider.tax_id"]

    def test_missing_patient_date_of_birth_refuses(self) -> None:
        findings = _refusal(patient=person(date_of_birth=None))
        assert [f.field for f in findings] == ["patient.date_of_birth"]

    def test_line_without_a_service_code_is_reported_not_dropped(self) -> None:
        findings = _refusal(claims=[claim(), _second_claim(lines=[_second_line(cpt="  ")])])
        assert [f.field for f in findings] == ["lines[1].cpt"]
        assert "2026-09-08" in findings[0].message

    def test_line_whose_pointers_name_no_diagnosis_is_reported(self) -> None:
        findings = _refusal(claims=[claim(diagnosis_codes=[]), _second_claim()])
        assert [f.field for f in findings] == ["lines[0].diagnosis_codes"]

    def test_line_with_no_fee_is_reported(self) -> None:
        findings = _refusal(claims=[claim(lines=[line(charge_cents=0)]), _second_claim()])
        assert [(f.code, f.field) for f in findings] == [("charge_zero", "lines[0].charge_cents")]

    def test_every_gap_is_listed_at_once(self) -> None:
        findings = _refusal(
            tax_id=None,
            billing=billing_snapshot(npi=None),
            claims=[claim(diagnosis_codes=[])],
        )
        assert [f.field for f in findings] == [
            "rendering_provider.npi",
            "billing_provider.tax_id",
            "lines[0].diagnosis_codes",
        ]

    def test_a_visit_with_no_code_says_to_fix_the_appointment(self) -> None:
        findings = _refusal(
            claims=[], appointments=[_unclaimed(service_code=None)], rate_for=lambda _a: 1
        )
        assert [f.field for f in findings] == ["lines[0].cpt"]
        assert findings[0].message == (
            "The service on 2026-09-03 has no service code. Add it to the appointment."
        )

    def test_a_visit_with_no_fee_says_where_the_rate_is_set(self) -> None:
        findings = _refusal(claims=[], charges=[], appointments=[_unclaimed()])
        assert [(f.code, f.field) for f in findings] == [("charge_zero", "lines[0].charge_cents")]
        assert "rebuild the claim" not in findings[0].message

    @pytest.mark.parametrize("status", ["pending", "cancelled"])
    def test_a_cancelled_or_requested_slot_is_not_a_visit(self, status: str) -> None:
        not_a_visit = _appointment(
            id="99999999-9999-4999-8999-999999999999",
            start_at=datetime(2026, 9, 3, 19, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
            status=status,
        )
        assert _build(appointments=[not_a_visit]).total_charge_cents == 31000

    def test_a_future_visit_is_not_printed(self) -> None:
        upcoming = _appointment(
            id="99999999-9999-4999-8999-999999999999",
            start_at=datetime(2026, 9, 20, 19, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 20, 20, 0, tzinfo=UTC),
        )
        assert _build(appointments=[upcoming]).total_charge_cents == 31000

    def test_a_period_with_no_visits_refuses(self) -> None:
        findings = _refusal(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31))
        assert [(f.code, f.field) for f in findings] == [("no_visits", "period")]


def _second_line(**overrides: Any) -> Any:
    return _second_claim().lines[0].model_copy(update=overrides)


_UNCLAIMED_ID = "99999999-9999-4999-8999-999999999999"


def _unclaimed(**overrides: Any) -> Appointment:
    """A self-pay visit on the 3rd that no claim was built for."""
    fields: dict[str, Any] = {
        "id": _UNCLAIMED_ID,
        "start_at": datetime(2026, 9, 3, 19, 0, tzinfo=UTC),
        "end_at": datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
        "service_code": "90837",
        "modifiers": ["95"],
        "unit_count": 1,
        "diagnosis_codes": ["F41.1"],
    }
    fields.update(overrides)
    return _appointment(**fields)


# ---------------------------------------------------------------------------
# A visit no claim was built for
# ---------------------------------------------------------------------------


class TestVisitWithoutAClaim:
    def test_prints_from_the_appointment(self) -> None:
        superbill = _build(
            claims=[],
            charges=[_charge(appointment_id=_UNCLAIMED_ID, amount_cents=11500)],
            appointments=[_unclaimed()],
        )
        (entry,) = superbill.lines
        assert (entry.service_date, entry.cpt, entry.modifiers, entry.units) == (
            date(2026, 9, 3),
            "90837",
            ("95",),
            1,
        )
        assert (entry.charge_cents, entry.paid_cents) == (11500, 11500)
        assert (entry.claim_id, entry.line_id, entry.appointment_id) == (None, None, _UNCLAIMED_ID)

    def test_fee_is_the_session_charge_the_statement_shows(self) -> None:
        # A sliding-scale visit charged at $90 prints $90, not the list rate.
        superbill = _build(
            claims=[],
            charges=[_charge(appointment_id=_UNCLAIMED_ID, amount_cents=9000)],
            appointments=[_unclaimed()],
            rate_for=lambda _appointment: 15000,
        )
        assert superbill.lines[0].charge_cents == 9000

    def test_fee_falls_back_to_the_rate_before_the_visit_is_charged(self) -> None:
        superbill = _build(
            claims=[], charges=[], appointments=[_unclaimed()], rate_for=lambda _a: 15000
        )
        assert (superbill.lines[0].charge_cents, superbill.lines[0].paid_cents) == (15000, 0)

    def test_a_copay_is_not_the_visit_fee(self) -> None:
        superbill = _build(
            claims=[],
            charges=[_charge(appointment_id=_UNCLAIMED_ID, amount_cents=2500, kind="copay")],
            appointments=[_unclaimed()],
            rate_for=lambda _a: 15000,
        )
        assert (superbill.lines[0].charge_cents, superbill.lines[0].paid_cents) == (15000, 2500)

    def test_a_visit_a_claim_covers_prints_once_from_the_claim(self) -> None:
        superbill = _build(appointments=[_appointment(service_code="90834")])
        assert [(entry.claim_id, entry.cpt) for entry in superbill.lines] == [
            (claim().id, "90837"),
            (_SECOND_CLAIM_ID, "90837"),
        ]

    def test_claimed_and_unclaimed_visits_print_in_date_order(self) -> None:
        superbill = _build(appointments=[_unclaimed()], rate_for=lambda _a: 15000)
        assert [entry.service_date for entry in superbill.lines] == [
            date(2026, 9, 1),
            date(2026, 9, 3),
            date(2026, 9, 8),
        ]
        assert superbill.appointment_ids == (
            APPOINTMENT_ID,
            _UNCLAIMED_ID,
            _SECOND_APPOINTMENT_ID,
        )
        assert superbill.claim_ids == (claim().id, _SECOND_CLAIM_ID)

    def test_visit_date_is_the_practice_local_date(self) -> None:
        # 03:00 UTC on the 4th is the evening of the 3rd in New York; a period
        # ending on the 3rd still covers it.
        late = _unclaimed(
            start_at=datetime(2026, 9, 4, 3, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 4, 4, 0, tzinfo=UTC),
        )
        superbill = _build(
            claims=[], period_end=date(2026, 9, 3), appointments=[late], rate_for=lambda _a: 1
        )
        assert [entry.service_date for entry in superbill.lines] == [date(2026, 9, 3)]


# ---------------------------------------------------------------------------
# Whether the diagnosis is printed
# ---------------------------------------------------------------------------


class TestDiagnosisChoice:
    def test_left_off_the_lines_carry_no_diagnosis(self) -> None:
        superbill = _build(
            includes_diagnosis=False, appointments=[_unclaimed()], rate_for=lambda _a: 1
        )
        assert [entry.diagnosis_codes for entry in superbill.lines] == [(), (), ()]

    def test_left_off_a_missing_diagnosis_is_not_a_gap(self) -> None:
        superbill = _build(
            includes_diagnosis=False,
            claims=[claim(diagnosis_codes=[])],
            appointments=[_unclaimed(diagnosis_codes=None)],
            rate_for=lambda _a: 15000,
        )
        assert len(superbill.lines) == 2

    def test_printed_a_visit_carries_the_appointment_diagnoses(self) -> None:
        superbill = _build(
            claims=[],
            appointments=[_unclaimed(diagnosis_codes=["F41.1", "F33.1"])],
            rate_for=lambda _a: 15000,
        )
        assert superbill.lines[0].diagnosis_codes == ("F41.1", "F33.1")

    def test_printed_a_visit_with_none_refuses_and_offers_leaving_it_off(self) -> None:
        findings = _refusal(
            claims=[], appointments=[_unclaimed(diagnosis_codes=None)], rate_for=lambda _a: 1
        )
        assert [f.field for f in findings] == ["lines[0].diagnosis_codes"]
        assert 'turn off "Include the diagnosis" for this client' in findings[0].message

    def test_left_off_the_page_has_no_diagnosis_column(self) -> None:
        text = render_superbill_pdf(_build(includes_diagnosis=False), _CLIENTS).decode("latin-1")
        assert "Diagnosis" not in text
        assert "F41.1" not in text

    def test_printed_the_page_has_the_column(self) -> None:
        text = render_superbill_pdf(_build(), _CLIENTS).decode("latin-1")
        assert "Diagnosis" in text
        assert "F41.1, F33.1" in text


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_the_pdf_carries_every_block(self) -> None:
        pdf = render_superbill_pdf(_build(), _CLIENTS)
        assert pdf.startswith(b"%PDF")
        text = pdf.decode("latin-1")
        for expected in (
            "Pablo Test Practice",
            "123 Some St",
            "123456789",  # the practice's tax id
            "Jane Smith",
            "1999999984",
            "101YM0800X",
            "LCSW-4321 GA",
            "John Anon",
            "01/01/2000",
            "09/01/2026",
            "90837 95",
            "F41.1, F33.1",
            "$150.00",
            "$160.00",
            "$310.00",
            "Therapy session",
            "Issued 09/06/2026 12:00 UTC",
        ):
            assert expected in text, expected

    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        assert render_superbill_pdf(_build(), _CLIENTS) == render_superbill_pdf(_build(), _CLIENTS)

    def test_a_different_period_gives_different_bytes(self) -> None:
        assert render_superbill_pdf(_build(), _CLIENTS) != render_superbill_pdf(
            _build(period_end=date(2026, 9, 5)), _CLIENTS
        )


# ---------------------------------------------------------------------------
# No model anywhere near this
# ---------------------------------------------------------------------------

_MODEL_CLIENT_MARKERS = (
    "vertex_client",
    "llm_gateway",
    "llm_provider",
    "google.genai",
    "anthropic",
    "openai",
    "mistralai",
)


def test_importing_the_renderer_loads_no_model_client() -> None:
    """Run in a fresh interpreter so the test process's own imports do not leak in."""
    probe = (
        "import sys, app.claims.superbill; "
        f"print([m for m in sys.modules if any(k in m for k in {_MODEL_CLIENT_MARKERS!r})])"
    )
    backend = Path(__file__).resolve().parent.parent
    result = subprocess.run(  # noqa: S603 — fixed argv: this interpreter and a literal, no shell
        [sys.executable, "-c", probe],
        check=True,
        capture_output=True,
        text=True,
        cwd=backend,
        env={"PYTHONPATH": str(backend), "PATH": "/usr/bin:/bin"},
    )
    assert result.stdout.strip() == "[]", result.stdout


def test_build_and_render_call_no_model(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        msg = "A superbill must never reach a model."
        raise AssertionError(msg)

    monkeypatch.setattr(vertex_client, "vertex_genai_client", forbidden)
    monkeypatch.setattr(vertex_client, "anthropic_vertex_client", forbidden)
    monkeypatch.setattr(structured_llm_gateway, "get_default_structured_llm_gateway", forbidden)
    monkeypatch.setattr(structured_llm_gateway, "resolve_structured_llm_gateway", forbidden)
    assert render_superbill_pdf(_build(), _CLIENTS).startswith(b"%PDF")
