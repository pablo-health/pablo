# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When a refill request's client runs out, and which medications are harmful to interrupt.

Pure functions, no database: :mod:`app.models.refill_risk`. The routes and
the in-memory repository that feeds it are covered in
``test_refill_requests_api.py``, the Postgres one in
``tests_integration/database/test_refill_requests_rls.py``, and the inbox item
that carries it at the end of this file.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.inbox.sources.refills import _item
from app.models.refill_request import RefillQueueEntry, RefillRequest, RefillRisk
from app.models.refill_risk import (
    INTERRUPTION_RISK_DEFAULTS,
    PrescriptionOnFile,
    assess,
    interruption_risk_by_default,
    is_controlled_schedule,
    run_out_from_days_left,
    run_out_from_prescription,
    same_drug,
)
from app.utcnow import utc_now

_TODAY = date(2026, 10, 20)


class TestRunOut:
    def test_days_left_counts_from_today(self) -> None:
        assert run_out_from_days_left(_TODAY, 0) == _TODAY
        assert run_out_from_days_left(_TODAY, 3) == date(2026, 10, 23)

    def test_a_prescription_lasts_its_supply_for_the_first_fill_and_each_refill(self) -> None:
        assert run_out_from_prescription(date(2026, 9, 1), 30, 0) == date(2026, 10, 1)
        assert run_out_from_prescription(date(2026, 9, 1), 30, 2) == date(2026, 11, 30)

    def test_the_clients_answer_wins_over_the_estimate(self) -> None:
        risk = assess(
            medication="Sertraline 50 mg",
            client_run_out=date(2026, 10, 22),
            prescription=PrescriptionOnFile(date(2026, 9, 1), "none", 30, 0),
        )
        assert (risk.run_out_date, risk.run_out_source) == (date(2026, 10, 22), "client")

    def test_the_estimate_from_fill_math_when_the_client_did_not_say(self) -> None:
        risk = assess(
            medication="Sertraline 50 mg",
            client_run_out=None,
            prescription=PrescriptionOnFile(date(2026, 9, 1), "none", 30, 1),
        )
        assert (risk.run_out_date, risk.run_out_source) == (date(2026, 10, 31), "prescription")

    def test_a_prescription_with_no_days_supply_estimates_nothing(self) -> None:
        risk = assess(
            medication="Sertraline",
            client_run_out=None,
            prescription=PrescriptionOnFile(date(2026, 9, 1), "none", None, 3),
        )
        assert (risk.run_out_date, risk.run_out_source) == (None, None)


class TestControlled:
    @pytest.mark.parametrize("schedule", ["II", "III", "IV", "V"])
    def test_schedules_ii_to_v_are_controlled(self, schedule: str) -> None:
        assert is_controlled_schedule(schedule)

    def test_none_is_not(self) -> None:
        assert not is_controlled_schedule("none")

    def test_a_schedule_ii_stimulant_is_controlled_but_not_harmful_to_interrupt(self) -> None:
        # A DEA schedule measures misuse potential, not the harm of a missed dose.
        risk = assess(
            medication="Methylphenidate 10 mg",
            client_run_out=None,
            prescription=PrescriptionOnFile(date(2026, 10, 1), "II", 30, 0),
        )
        assert risk.controlled is True
        assert risk.interruption_risk is False

    def test_the_prescriptions_schedule_wins_over_the_clinicians_flag(self) -> None:
        risk = assess(
            medication="Methylphenidate",
            client_run_out=None,
            prescription=PrescriptionOnFile(date(2026, 10, 1), "II", 30, 0),
            clinician_controlled=False,
        )
        assert risk.controlled is True

    def test_with_no_prescription_the_clinicians_flag_decides(self) -> None:
        assert assess(medication="X", client_run_out=None, clinician_controlled=True).controlled
        assert not assess(medication="X", client_run_out=None).controlled


class TestInterruptionRisk:
    @pytest.mark.parametrize(
        "medication",
        [
            "Lithium carbonate ER 300 mg",
            "LAMICTAL XR 100 mg",
            "clozapine",
            "Clonazepam 0.5 mg",
            "gabapentin 300mg",
            "Pregabalin",
            "Divalproex sodium DR",
            "Carbamazepine",
            "Clonidine 0.1 mg",
            "Guanfacine ER 2 mg",
            "Intuniv",
            "Buprenorphine/naloxone",
            "Methadone",
            "Quetiapine 50 mg",
            "Phenobarbital",
        ],
    )
    def test_a_default_list_member_is_harmful_to_interrupt(self, medication: str) -> None:
        assert interruption_risk_by_default(medication)

    @pytest.mark.parametrize(
        "medication",
        ["Sertraline 50 mg", "Paroxetine", "Venlafaxine XR", "Methylphenidate", "Hydroxyzine"],
    )
    def test_an_ordinary_medication_is_not(self, medication: str) -> None:
        assert not interruption_risk_by_default(medication)

    def test_matches_whole_words_only(self) -> None:
        # "valium" must not match inside an unrelated longer word.
        assert not interruption_risk_by_default("Valiumx")

    def test_the_clinicians_flag_wins_either_way(self) -> None:
        assert not assess(
            medication="Lithium", client_run_out=None, clinician_interruption_risk=False
        ).interruption_risk
        assert assess(
            medication="Sertraline", client_run_out=None, clinician_interruption_risk=True
        ).interruption_risk

    def test_every_entry_names_at_least_one_drug_in_lowercase(self) -> None:
        for entry in INTERRUPTION_RISK_DEFAULTS:
            assert entry.names
            assert all(name == name.lower() and name.isalpha() for name in entry.names)


class TestSameDrug:
    def test_by_first_word(self) -> None:
        assert same_drug("Lithium carbonate", "lithium carbonate ER 300 mg")
        assert not same_drug("Lithium", "Lamotrigine 25 mg")
        assert not same_drug("", "Lithium")


class TestInboxItemCarriesRisk:
    def _entry(self, risk: RefillRisk) -> RefillQueueEntry:
        now = utc_now()
        request = RefillRequest(
            id="r1",
            patient_id="p1",
            medication_id=None,
            medication_text="Lithium 300 mg",
            pharmacy_text=None,
            patient_note=None,
            status="requested",
            created_at=now,
            updated_at=now,
        )
        return RefillQueueEntry(request, "Ada", "Lovelace", None, risk=risk)

    def test_run_out_and_flags_as_strings(self) -> None:
        item = _item(
            self._entry(
                RefillRisk(
                    run_out_date=date(2026, 10, 22),
                    run_out_source="client",
                    controlled=False,
                    interruption_risk=True,
                )
            )
        )
        assert item.context == {
            "controlled": "false",
            "interruption_risk": "true",
            "run_out_date": "2026-10-22",
            "run_out_source": "client",
        }

    def test_no_run_out_date_means_no_key(self) -> None:
        item = _item(self._entry(RefillRisk()))
        assert "run_out_date" not in item.context
