# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Schema-contract guards for the prescribing encounter context model.

These pin the column names and value vocabularies the prescribing rules
engine evaluates against — the contract the enforcement evaluator and the
curated rulesets resolve their ``trigger`` / ``satisfied_when`` field paths
against. A column rename or a dropped schedule token here would silently
break rule applicability downstream, so assert the shape explicitly.
"""

from __future__ import annotations

from app.db.models import (
    ENCOUNTER_MODALITIES,
    ENCOUNTER_STATUSES,
    PRESCRIPTION_DRUG_CLASSES,
    PRESCRIPTION_SCHEDULES,
    Base,
    PrescribingEncounterRow,
    PrescriptionRow,
)


def test_value_vocabularies_match_engine_tokens() -> None:
    # These mirror the rules-engine RuleContext dimensions; the curated
    # rulesets gate items on exactly these tokens.
    assert PRESCRIPTION_SCHEDULES == ("II", "III", "IV", "V", "none")
    assert PRESCRIPTION_DRUG_CLASSES == (
        "opioid",
        "stimulant",
        "benzodiazepine",
        "buprenorphine",
        "other",
    )
    assert ENCOUNTER_STATUSES == ("open", "finalized", "voided")
    assert "audio_video" in ENCOUNTER_MODALITIES
    assert "in_person" in ENCOUNTER_MODALITIES


def test_tables_registered_on_tenant_base() -> None:
    assert "prescribing_encounters" in Base.metadata.tables
    assert "prescriptions" in Base.metadata.tables


def test_encounter_carries_context_and_snapshot_columns() -> None:
    cols = set(PrescribingEncounterRow.__table__.columns.keys())
    # Rules-engine context dimensions + conditional-trigger facts.
    assert {"state", "modality", "prior_in_person", "patient_in_sud_program"} <= cols
    # Snapshotted prescriber + delegating-physician (contemporaneous dual-DEA).
    assert {
        "prescriber_user_id",
        "prescriber_type",
        "prescriber_dea",
        "prescriber_license",
        "delegation_ref",
        "delegating_physician_name",
        "delegating_physician_dea",
    } <= cols
    # Stamped with the ruleset in force; finalization columns shipped now.
    assert {"ruleset_version", "status", "finalized_at", "encountered_at"} <= cols
    # Patient-scoped (RLS via has_patient_access), not nullable.
    assert PrescribingEncounterRow.__table__.c.patient_id.nullable is False


def test_prescription_carries_evaluated_fields() -> None:
    cols = set(PrescriptionRow.__table__.columns.keys())
    assert {"schedule", "drug_class", "quantity", "days_supply", "refills"} <= cols
    # Conditional-rule triggers live on the prescription.
    assert {"indication", "first_in_course"} <= cols
    # schedule / drug_class are required (they select applicability).
    assert PrescriptionRow.__table__.c.schedule.nullable is False
    assert PrescriptionRow.__table__.c.drug_class.nullable is False


def test_foreign_keys_wire_encounter_and_patient() -> None:
    enc_fk_targets = {
        fk.target_fullname for col in PrescriptionRow.__table__.columns for fk in col.foreign_keys
    }
    assert "prescribing_encounters.id" in enc_fk_targets
    assert "patients.id" in enc_fk_targets

    patient_fks = {
        fk.target_fullname
        for col in PrescribingEncounterRow.__table__.columns
        for fk in col.foreign_keys
    }
    assert "patients.id" in patient_fks


def test_check_constraints_present() -> None:
    enc_checks = {
        c.name
        for c in PrescribingEncounterRow.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert {
        "ck_prescribing_encounters_status",
        "ck_prescribing_encounters_modality",
    } <= enc_checks

    rx_checks = {
        c.name
        for c in PrescriptionRow.__table__.constraints
        if c.__class__.__name__ == "CheckConstraint"
    }
    assert {"ck_prescriptions_schedule", "ck_prescriptions_drug_class"} <= rx_checks
