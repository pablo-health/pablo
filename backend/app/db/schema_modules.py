# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Schema modules: which slice of a practice schema each per-practice table is in.

Every practice gets its own schema, built from ``tenant_template.sql``. A
schema module is a named slice of that schema: a set of per-practice tables
that a practice holds or does not hold as a unit. A practice records the
modules it holds in ``platform.practices.schema_modules``.

The engine defines the module names. ``clinical`` is the core, and every
practice holds it. A deployment that adds per-practice tables through its own
migration chain assigns each of them to one of these modules with
:func:`register_module_tables`, the same way it registers its tables with the
row-level security registries.

The default, for a new practice and for every practice that existed before the
column did, is every module. A deployment that never narrows it behaves exactly
as one built before modules existed.

Two rules follow from what the schema is:

* **A module is never removed from a practice.** Removing one would not drop
  its tables, and a practice keeps migrating every module it has ever held, so
  ``schema_modules`` only grows.
* **Modules joined by a foreign key are held together.** No foreign key points
  from ``clinical`` into another module, so a practice can hold ``clinical``
  alone. ``ai`` and ``portal`` point into each other
  (``note_chart_proposals`` references ``patient_intake_assignments``, and
  ``homework_assignments`` references ``note_chart_proposals``), so neither can
  be built without the other. :data:`MODULE_REQUIRES` records that, and
  :func:`with_required` closes a set of modules over it.

No PHI: module names and table names.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: The core module. Every practice holds it.
CLINICAL: Final = "clinical"
#: AI-assisted work: chat, dictation, note-to-chart proposals, prescribing.
AI: Final = "ai"
#: What a practice's clients use: intake, the companion app, homework, refills.
PORTAL: Final = "portal"

#: Every module the engine defines, in the order a practice's schema is built.
MODULES: Final[tuple[str, ...]] = (CLINICAL, AI, PORTAL)

#: The modules each module needs present before its tables can be built: the
#: modules its tables' foreign keys point into. ``clinical`` needs nothing.
#: ``ai`` and ``portal`` point into each other, so each requires the other.
#: ``test_schema_modules`` derives the foreign keys from the template and holds
#: this table to them.
MODULE_REQUIRES: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        CLINICAL: frozenset(),
        AI: frozenset({CLINICAL, PORTAL}),
        PORTAL: frozenset({CLINICAL, AI}),
    }
)


def _assign(module: str, tables: str) -> dict[str, str]:
    return dict.fromkeys(tables.split(), module)


#: The module of every per-practice table the engine creates: the tables in
#: ``tenant_template.sql``, one module each. ``alembic_version`` is listed
#: because it lives in the practice schema like any other table.
TABLE_MODULE: Final[Mapping[str, str]] = MappingProxyType(
    {
        **_assign(
            CLINICAL,
            """
            alembic_version allowed_emails appointment_types appointments
            audit_logs availability_rules claim_events claim_lines
            claim_reminders claims client_ai_consent_events
            clinician_away_periods clinician_profiles compliance_documents
            diagnostic_assessments external_calendar_events
            google_calendar_settings google_calendar_tokens
            google_created_calendars ical_sync_configs import_records
            import_runs inbox_item_states instrument_license_attestations
            note_addenda note_signatures notes outcome_measures
            patient_charges patient_chart_history
            patient_chart_history_revisions patient_clinicians
            patient_coverage patient_documents patient_medications
            patient_message_attachments patient_message_threads
            patient_messages patient_payment_methods patient_preferences
            patient_problems patient_source_mappings patients
            payer_enrollments payers practice_admins
            practice_billing_profile practice_note_types remittance_holds
            scheduling_policy supervision_hours supervision_relationships
            tasks telehealth_connections therapy_sessions waitlist_entries
            """,
        ),
        **_assign(
            AI,
            """
            chat_conversations chat_messages ehr_prompts ehr_routes
            llm_usage note_chart_proposal_runs note_chart_proposals
            prescribing_checklist_items prescribing_encounter_addenda
            prescribing_encounters prescriptions session_dictations
            """,
        ),
        **_assign(
            PORTAL,
            """
            companion_auth_challenges companion_sessions
            homework_assignments intake_blank_forms intake_documents
            intake_item_definitions intake_packet_templates
            intake_packet_versions patient_intake_artifacts
            patient_intake_assignments patient_intake_responses
            patient_intake_review_events patient_intake_signatures
            patient_intake_submissions refill_requests
            """,
        ),
    }
)

#: Per-practice tables a deployment created through its own chain, by module.
#: Filled by :func:`register_module_tables`; the engine registers none.
_registered_tables: dict[str, str] = {}


class UnknownSchemaModuleError(ValueError):
    """A module name the engine does not define."""


def _require_known(module: str) -> None:
    if module not in MODULES:
        raise UnknownSchemaModuleError(
            f"Unknown schema module {module!r}; the modules are {', '.join(MODULES)}"
        )


def register_module_tables(mapping: Mapping[str, str]) -> None:
    """Assign a deployment's own per-practice tables to modules.

    For tables a deployment adds to every practice schema through its own
    migration chain. Each table goes to exactly one module the engine defines.

    Re-registering a table under the module it already has is a no-op, so a
    process that registers on every start is safe. Registering it under a
    different module, or registering a table the engine already assigns, is
    refused: a table in two modules would be built twice or not at all.
    """
    for table, module in mapping.items():
        _require_known(module)
        if table in TABLE_MODULE:
            raise ValueError(
                f"Table {table!r} is the engine's, in module {TABLE_MODULE[table]!r}; "
                "a deployment cannot reassign it"
            )
        current = _registered_tables.get(table)
        if current is not None and current != module:
            raise ValueError(
                f"Table {table!r} is already registered in module {current!r}, not {module!r}"
            )
    _registered_tables.update(mapping)


def reset_registered_module_tables() -> None:
    """Forget every deployment registration. For tests; do not call from prod."""
    _registered_tables.clear()


def module_of(table: str) -> str | None:
    """The module ``table`` belongs to, or ``None`` when nothing registered it."""
    return TABLE_MODULE.get(table) or _registered_tables.get(table)


def tables_in(module: str) -> frozenset[str]:
    """Every registered per-practice table in ``module``, the engine's and a deployment's."""
    _require_known(module)
    return frozenset(
        table
        for registry in (TABLE_MODULE, _registered_tables)
        for table, owner in registry.items()
        if owner == module
    )


def with_required(modules: Iterable[str]) -> tuple[str, ...]:
    """``modules`` with ``clinical`` and everything they require, in build order.

    Refuses a name the engine does not define rather than dropping it: a
    practice recorded with a module the engine does not know has tables nobody
    will migrate.
    """
    wanted = {CLINICAL}
    pending = list(modules)
    while pending:
        module = pending.pop()
        _require_known(module)
        if module not in wanted:
            wanted.add(module)
            pending.extend(MODULE_REQUIRES[module])
    return tuple(m for m in MODULES if m in wanted)


class _HoldsModules(Protocol):
    @property
    def schema_modules(self) -> list[str]: ...


def modules_for(practice: _HoldsModules) -> tuple[str, ...]:
    """The modules ``practice`` holds, in build order.

    Reads ``platform.practices.schema_modules`` off a loaded practice row, and
    closes it over :data:`MODULE_REQUIRES`, so a caller always gets a set it
    can build. The column defaults to every module, so an ordinary practice
    gets :data:`MODULES`.
    """
    return with_required(practice.schema_modules)


__all__ = [
    "AI",
    "CLINICAL",
    "MODULES",
    "MODULE_REQUIRES",
    "PORTAL",
    "TABLE_MODULE",
    "UnknownSchemaModuleError",
    "module_of",
    "modules_for",
    "register_module_tables",
    "reset_registered_module_tables",
    "tables_in",
    "with_required",
]
