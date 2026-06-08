# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the attestation context/facts builders (no database).

These pin the contract the curated ruleset ``trigger`` / ``satisfied_when``
predicates resolve against: the dotted ``facts`` paths and the applicability
:class:`RuleContext` that :mod:`app.prescribing.attestation` assembles from a
prescribing encounter + prescription. A drift here silently breaks rule
applicability downstream, so assert the mapping explicitly.

The ledger persistence (``sync_checklist`` / ``bind_evidence``) is exercised
against real Postgres in
``tests_integration/database/test_prescribing_attestation_rls.py``.
"""

from __future__ import annotations

from app.db.models import PrescribingEncounterRow, PrescriptionRow
from app.prescribing.attestation import build_facts, build_rule_context


def _encounter(**overrides: object) -> PrescribingEncounterRow:
    base: dict[str, object] = {
        "id": "enc-1",
        "patient_id": "pat-1",
        "prescriber_user_id": "u-1",
        "prescriber_type": "pmhnp",
        "prescriber_npi": "1234567890",
        "prescriber_dea": "BX1234567",
        "prescriber_license": "MI-APRN-9",
        "state": "MI",
        "modality": "audio_video",
        "prior_in_person": False,
        "patient_in_sud_program": False,
        "status": "open",
    }
    base.update(overrides)
    return PrescribingEncounterRow(**base)


def _prescription(**overrides: object) -> PrescriptionRow:
    base: dict[str, object] = {
        "id": "rx-1",
        "encounter_id": "enc-1",
        "patient_id": "pat-1",
        "schedule": "II",
        "drug_class": "stimulant",
        "strength": "10 mg",
        "quantity": 30,
        "days_supply": 30,
        "refills": 0,
        "indication": "adhd",
        "first_in_course": True,
    }
    base.update(overrides)
    return PrescriptionRow(**base)


def test_rule_context_selects_on_provider_state_schedule_class() -> None:
    ctx = build_rule_context(_encounter(), _prescription())
    assert ctx.provider_type == "pmhnp"
    assert ctx.state == "MI"
    assert ctx.schedule == "II"
    assert ctx.drug_class == "stimulant"


def test_facts_carry_every_documented_path() -> None:
    facts = build_facts(_encounter(), _prescription())
    assert facts == {
        "prescription.schedule": "II",
        "prescription.drug_class": "stimulant",
        "prescription.days_supply": 30,
        "prescription.refills": 0,
        "prescription.quantity": 30,
        "prescription.strength": "10 mg",
        "context.state": "MI",
        "context.modality": "audio_video",
        "context.prior_in_person": False,
        "context.patient_in_sud_program": False,
        "context.indication": "adhd",
        "context.first_in_course": True,
        "prescriber.type": "pmhnp",
        "prescriber.dea": "BX1234567",
        "prescriber.license": "MI-APRN-9",
        "prescriber.npi": "1234567890",
        "prescriber.delegation_status": None,
    }


def test_delegation_status_reflects_delegation_ref() -> None:
    delegated = build_facts(_encounter(delegation_ref="supervision-row-7"), _prescription())
    assert delegated["prescriber.delegation_status"] == "delegated"

    independent = build_facts(_encounter(delegation_ref=None), _prescription())
    assert independent["prescriber.delegation_status"] is None
