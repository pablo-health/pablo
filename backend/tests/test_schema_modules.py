# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The schema module registry: which module each per-practice table is in.

The assignment is written out here by module, table by table, so a change to it
is a change to this file too. The other tests hold the registry to the tenant
template: every table it creates has exactly one module, and every foreign key
that crosses modules is one :data:`MODULE_REQUIRES` knows about.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from app.db import schema_modules
from app.db.platform_models import PracticeRow
from app.db.provisioning import _TENANT_TEMPLATE_SQL_PATH
from app.db.schema_modules import (
    AI,
    CLINICAL,
    MODULE_REQUIRES,
    MODULES,
    PORTAL,
    TABLE_MODULE,
    UnknownSchemaModuleError,
    module_of,
    modules_for,
    register_module_tables,
    tables_in,
    with_required,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_EXPECTED: dict[str, set[str]] = {
    CLINICAL: {
        "alembic_version",
        "allowed_emails",
        "appointment_types",
        "appointments",
        "audit_logs",
        "availability_rules",
        "claim_events",
        "claim_lines",
        "claim_reminders",
        "claims",
        "client_ai_consent_events",
        "clinician_away_periods",
        "clinician_profiles",
        "compliance_documents",
        "diagnostic_assessments",
        "external_calendar_events",
        "google_calendar_settings",
        "google_calendar_tokens",
        "google_created_calendars",
        "ical_sync_configs",
        "import_records",
        "import_runs",
        "inbox_item_states",
        "instrument_license_attestations",
        "note_addenda",
        "note_signatures",
        "notes",
        "outcome_measures",
        "patient_charges",
        "patient_chart_history",
        "patient_chart_history_revisions",
        "patient_clinicians",
        "patient_coverage",
        "patient_documents",
        "patient_medications",
        "patient_message_attachments",
        "patient_message_threads",
        "patient_messages",
        "patient_payment_methods",
        "patient_preferences",
        "patient_problems",
        "patient_source_mappings",
        "patients",
        "payer_enrollments",
        "payers",
        "practice_admins",
        "practice_billing_profile",
        "practice_note_types",
        "remittance_holds",
        "scheduling_policy",
        "supervision_hours",
        "supervision_relationships",
        "tasks",
        "telehealth_connections",
        "therapy_sessions",
        "waitlist_entries",
    },
    AI: {
        "chat_conversations",
        "chat_messages",
        "ehr_prompts",
        "ehr_routes",
        "llm_usage",
        "note_chart_proposal_runs",
        "note_chart_proposals",
        "prescribing_checklist_items",
        "prescribing_encounter_addenda",
        "prescribing_encounters",
        "prescriptions",
        "session_dictations",
    },
    PORTAL: {
        "companion_auth_challenges",
        "companion_sessions",
        "homework_assignments",
        "intake_blank_forms",
        "intake_documents",
        "intake_item_definitions",
        "intake_packet_templates",
        "intake_packet_versions",
        "patient_intake_artifacts",
        "patient_intake_assignments",
        "patient_intake_responses",
        "patient_intake_review_events",
        "patient_intake_signatures",
        "patient_intake_submissions",
        "refill_requests",
    },
}

_PLATFORM_REVISION = (
    Path(__file__).resolve().parents[1]
    / "alembic_platform"
    / "versions"
    / "c4e8a2f6d1b9_practice_schema_modules.py"
)


def _template() -> str:
    return _TENANT_TEMPLATE_SQL_PATH.read_text()


def _template_tables() -> set[str]:
    return set(re.findall(r"^CREATE TABLE __TENANT_SCHEMA__\.(\w+) \(", _template(), re.M))


def _template_foreign_keys() -> set[tuple[str, str]]:
    """``(from_table, to_table)`` for every foreign key in the template."""
    return set(
        re.findall(
            r"^ALTER TABLE ONLY __TENANT_SCHEMA__\.(\w+)\s+"
            r"ADD CONSTRAINT \w+ FOREIGN KEY \([^)]*\) REFERENCES __TENANT_SCHEMA__\.(\w+)\(",
            _template(),
            re.M,
        )
    )


@pytest.fixture(autouse=True)
def _clean_registrations() -> Iterator[None]:
    schema_modules.reset_registered_module_tables()
    yield
    schema_modules.reset_registered_module_tables()


# --- the assignment -------------------------------------------------------


def test_the_assignment_is_the_designed_one_table_by_table() -> None:
    assert sum(len(t) for t in _EXPECTED.values()) == 83
    assert {m: set(tables_in(m)) for m in MODULES} == _EXPECTED
    assert {t: m for m, ts in _EXPECTED.items() for t in ts} == dict(TABLE_MODULE)


def test_every_template_table_has_exactly_one_module() -> None:
    tables = _template_tables()
    assert len(tables) == 83
    assert tables == set(TABLE_MODULE)


def test_the_foreign_key_parse_reads_the_template() -> None:
    # Guards the parse below: if the dump format moved, it would find nothing
    # and the requirements test would pass vacuously.
    fks = _template_foreign_keys()
    assert ("note_chart_proposals", "patient_intake_assignments") in fks
    assert ("homework_assignments", "note_chart_proposals") in fks
    assert len(fks) > 50


def test_no_foreign_key_leaves_clinical() -> None:
    leaving = {
        (a, b)
        for a, b in _template_foreign_keys()
        if TABLE_MODULE[a] == CLINICAL and TABLE_MODULE[b] != CLINICAL
    }
    assert leaving == set()


def test_every_cross_module_foreign_key_is_a_requirement() -> None:
    needed: dict[str, set[str]] = {m: set() for m in MODULES}
    for a, b in _template_foreign_keys():
        if TABLE_MODULE[a] != TABLE_MODULE[b]:
            needed[TABLE_MODULE[a]].add(TABLE_MODULE[b])
    for module in MODULES:
        assert needed[module] <= MODULE_REQUIRES[module], module
    # And nothing is required that no key asks for.
    assert {m: set(r) for m, r in MODULE_REQUIRES.items()} == needed


def test_the_registry_cannot_be_edited_in_place() -> None:
    with pytest.raises(TypeError):
        TABLE_MODULE["patients"] = AI  # type: ignore[index]
    with pytest.raises(TypeError):
        MODULE_REQUIRES[CLINICAL] = frozenset({AI})  # type: ignore[index]


# --- the record -----------------------------------------------------------


def test_the_column_default_is_every_module() -> None:
    column = PracticeRow.__table__.c.schema_modules
    assert column.nullable is False
    server_default = str(column.server_default.arg)  # type: ignore[union-attr]
    assert re.findall(r"'(\w+)'", server_default) == list(MODULES)
    assert column.default.arg(None) == list(MODULES)  # type: ignore[union-attr]


def test_the_migration_default_is_every_module() -> None:
    upgrade = _PLATFORM_REVISION.read_text().split("def upgrade", 1)[1]
    default = re.search(r"DEFAULT ARRAY\[([^\]]*)\]", upgrade)
    assert default is not None
    assert re.findall(r"'(\w+)'", default.group(1)) == list(MODULES)


# --- deployment registrations ---------------------------------------------


def test_a_deployment_registers_its_own_tables() -> None:
    register_module_tables({"overlay_notes": CLINICAL, "overlay_chat": AI})
    assert module_of("overlay_notes") == CLINICAL
    assert module_of("overlay_chat") == AI
    assert "overlay_chat" in tables_in(AI)
    assert "overlay_chat" not in tables_in(CLINICAL)
    assert module_of("never_registered") is None


def test_registering_the_same_table_twice_is_a_no_op() -> None:
    register_module_tables({"overlay_notes": PORTAL})
    register_module_tables({"overlay_notes": PORTAL})
    assert module_of("overlay_notes") == PORTAL


def test_a_registered_table_cannot_move_module() -> None:
    register_module_tables({"overlay_notes": PORTAL})
    with pytest.raises(ValueError, match="already registered"):
        register_module_tables({"overlay_notes": AI})
    assert module_of("overlay_notes") == PORTAL


def test_an_engine_table_cannot_be_reassigned() -> None:
    with pytest.raises(ValueError, match="engine's"):
        register_module_tables({"patients": PORTAL})
    with pytest.raises(ValueError, match="engine's"):
        register_module_tables({"patients": CLINICAL})


def test_an_unknown_module_is_refused() -> None:
    with pytest.raises(UnknownSchemaModuleError):
        register_module_tables({"overlay_notes": "billing"})
    with pytest.raises(UnknownSchemaModuleError):
        tables_in("billing")


def test_a_refused_registration_registers_nothing() -> None:
    with pytest.raises(UnknownSchemaModuleError):
        register_module_tables({"overlay_a": CLINICAL, "overlay_b": "billing"})
    assert module_of("overlay_a") is None


# --- what a practice holds ------------------------------------------------


class _Practice:
    def __init__(self, modules: list[str]) -> None:
        self.schema_modules = modules


def test_every_module_is_the_ordinary_case() -> None:
    assert modules_for(_Practice(list(MODULES))) == MODULES
    assert modules_for(_Practice([PORTAL, CLINICAL, AI])) == MODULES


def test_clinical_alone_is_a_complete_set() -> None:
    assert modules_for(_Practice([CLINICAL])) == (CLINICAL,)
    assert with_required([]) == (CLINICAL,)


@pytest.mark.parametrize("module", [AI, PORTAL])
def test_ai_and_portal_come_together(module: str) -> None:
    assert with_required([module]) == (CLINICAL, AI, PORTAL)


def test_a_practice_with_an_unknown_module_is_refused() -> None:
    with pytest.raises(UnknownSchemaModuleError):
        modules_for(_Practice([CLINICAL, "billing"]))
