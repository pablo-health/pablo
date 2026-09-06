# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the superbill build and render (``app.claims.superbill``).

What these pin down:

* the document is a render of the client's claims — one line per claim
  line in the period, with the date, code, pointed diagnoses, fee and what
  the ledger says was paid — and its totals are the sum of those lines;
* narrowing the period drops lines and the totals move with them;
* voids and corrected parents are left out, and a visit with two standing
  claims appears once;
* only a succeeded charge counts as paid, and a visit's payment is spread
  over its lines in line order;
* anything an insurer needs that is missing refuses the whole document,
  naming the field: the rendering NPI, a line's service code, its diagnosis,
  its fee, and a visit in the period with no claim at all;
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
    subscriber_snapshot,
)

_TZ = ZoneInfo("America/New_York")
_GENERATED_AT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)
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
        "claims": [claim(), _second_claim()],
        "charges": [_charge()],
        "appointments": [],
        "timezone": _TZ,
        "tax_id": _TAX_ID,
        "license_for": _license_for,
        "generated_at": _GENERATED_AT,
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
        findings = _refusal(claims=[claim(billing_snapshot=billing_snapshot(npi=None))])
        assert [(f.code, f.field) for f in findings] == [
            ("missing_field", "rendering_provider.npi"),
        ]

    def test_missing_tax_id_refuses(self) -> None:
        findings = _refusal(tax_id=None)
        assert [f.field for f in findings] == ["billing_provider.tax_id"]

    def test_missing_patient_date_of_birth_refuses(self) -> None:
        findings = _refusal(
            claims=[
                claim(subscriber_snapshot=subscriber_snapshot(patient=person(date_of_birth=None)))
            ]
        )
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
            claims=[claim(billing_snapshot=billing_snapshot(npi=None), diagnosis_codes=[])],
        )
        assert [f.field for f in findings] == [
            "rendering_provider.npi",
            "billing_provider.tax_id",
            "lines[0].diagnosis_codes",
        ]

    def test_visit_in_the_period_with_no_claim_is_reported(self) -> None:
        unclaimed = _appointment(
            id="99999999-9999-4999-8999-999999999999",
            start_at=datetime(2026, 9, 3, 19, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
        )
        findings = _refusal(appointments=[_appointment(), unclaimed])
        assert [(f.code, f.field) for f in findings] == [
            ("visit_without_claim", f"appointments[{unclaimed.id}]"),
        ]
        assert "2026-09-03" in findings[0].message

    def test_visit_date_is_the_practice_local_date(self) -> None:
        # 03:00 UTC on the 4th is the evening of the 3rd in New York; a period
        # ending on the 3rd still covers it, so it is a visit with no claim.
        late = _appointment(
            id="99999999-9999-4999-8999-999999999999",
            start_at=datetime(2026, 9, 4, 3, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 4, 4, 0, tzinfo=UTC),
        )
        findings = _refusal(period_end=date(2026, 9, 3), appointments=[late])
        assert [f.code for f in findings] == ["visit_without_claim"]

    @pytest.mark.parametrize("status", ["pending", "cancelled"])
    def test_a_cancelled_or_requested_slot_is_not_a_visit(self, status: str) -> None:
        not_a_visit = _appointment(
            id="99999999-9999-4999-8999-999999999999",
            start_at=datetime(2026, 9, 3, 19, 0, tzinfo=UTC),
            end_at=datetime(2026, 9, 3, 20, 0, tzinfo=UTC),
            status=status,
        )
        assert _build(appointments=[not_a_visit]).total_charge_cents == 31000

    def test_a_future_visit_is_not_reported(self) -> None:
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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


class TestRender:
    def test_the_pdf_carries_every_block(self) -> None:
        pdf = render_superbill_pdf(_build())
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
            "Generated 09/06/2026 12:00 UTC",
        ):
            assert expected in text, expected

    def test_the_same_inputs_give_the_same_bytes(self) -> None:
        assert render_superbill_pdf(_build()) == render_superbill_pdf(_build())

    def test_a_different_period_gives_different_bytes(self) -> None:
        assert render_superbill_pdf(_build()) != render_superbill_pdf(
            _build(period_end=date(2026, 9, 5))
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
    assert render_superbill_pdf(_build()).startswith(b"%PDF")
