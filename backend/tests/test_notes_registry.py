# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the note-type registry (pa-a5p.1)."""

from __future__ import annotations

import dataclasses

import pytest
from app.models.soap_note import (
    AssessmentNote,
    ObjectiveNote,
    PlanNote,
    SubjectiveNote,
)
from app.notes import (
    INTAKE_DEFINITION,
    MEDICATIONS_DEFINITION,
    NARRATIVE_DEFINITION,
    SAFETY_PLAN_DEFINITION,
    SOAP_DEFINITION,
    TREATMENT_PLAN_DEFINITION,
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
    register_builtin_note_types,
)
from app.services.chat_context_bundler import (
    INTAKE_NOTE_TYPES,
    MEDICATIONS_NOTE_TYPES,
    SAFETY_PLAN_NOTE_TYPES,
    TREATMENT_PLAN_NOTE_TYPES,
)

ALL_BUILTIN_DEFINITIONS = (
    SOAP_DEFINITION,
    NARRATIVE_DEFINITION,
    INTAKE_DEFINITION,
    TREATMENT_PLAN_DEFINITION,
    SAFETY_PLAN_DEFINITION,
    MEDICATIONS_DEFINITION,
)


def _tiny_type(key: str = "tiny") -> NoteTypeDefinition:
    return NoteTypeDefinition(
        key=key,
        label="Tiny",
        description="For tests.",
        tier="core",
        sections=(
            NoteSectionDef(
                key="only",
                label="Only",
                fields=(NoteFieldDef(key="body", label="Body", kind="text"),),
            ),
        ),
    )


class TestNoteTypeRegistry:
    def test_register_and_get(self) -> None:
        registry = NoteTypeRegistry()
        definition = _tiny_type()

        registry.register(definition)

        assert registry.get("tiny") is definition
        assert registry.has("tiny")

    def test_get_missing_raises_keyerror(self) -> None:
        registry = NoteTypeRegistry()

        with pytest.raises(KeyError):
            registry.get("nope")

    def test_duplicate_key_raises_unless_replace(self) -> None:
        registry = NoteTypeRegistry()
        registry.register(_tiny_type())

        with pytest.raises(ValueError, match="already registered"):
            registry.register(_tiny_type())

        replacement = NoteTypeDefinition(
            key="tiny",
            label="Tiny v2",
            description="replaced",
            sections=(),
        )
        registry.register(replacement, replace=True)
        assert registry.get("tiny") is replacement

    def test_all_returns_sorted_by_key(self) -> None:
        registry = NoteTypeRegistry()
        registry.register(_tiny_type(key="zulu"))
        registry.register(_tiny_type(key="alpha"))
        registry.register(_tiny_type(key="mike"))

        assert registry.keys() == ["alpha", "mike", "zulu"]
        assert [d.key for d in registry.all()] == ["alpha", "mike", "zulu"]

    def test_has_reports_membership(self) -> None:
        registry = NoteTypeRegistry()
        assert not registry.has("tiny")
        registry.register(_tiny_type())
        assert registry.has("tiny")

    def test_clear_drops_all(self) -> None:
        registry = NoteTypeRegistry()
        registry.register(_tiny_type())
        registry.clear()

        assert registry.keys() == []
        assert not registry.has("tiny")


class TestBuiltinDefinitions:
    def test_register_builtin_populates_all_core_types(self) -> None:
        registry = NoteTypeRegistry()

        register_builtin_note_types(registry)

        assert registry.keys() == [
            "intake",
            "medications",
            "narrative",
            "safety_plan",
            "soap",
            "treatment_plan",
        ]
        assert registry.get("soap") is SOAP_DEFINITION
        assert registry.get("narrative") is NARRATIVE_DEFINITION
        assert registry.get("intake") is INTAKE_DEFINITION
        assert registry.get("treatment_plan") is TREATMENT_PLAN_DEFINITION
        assert registry.get("safety_plan") is SAFETY_PLAN_DEFINITION
        assert registry.get("medications") is MEDICATIONS_DEFINITION

    def test_register_builtin_is_idempotent(self) -> None:
        registry = NoteTypeRegistry()

        register_builtin_note_types(registry)
        register_builtin_note_types(registry)

        assert len(registry.keys()) == 6

    def test_alias_keys_are_not_registered(self) -> None:
        """The bundler's alternate keys are recognised on read, not registered
        as their own note types — that would duplicate entries in every
        note-type picker."""
        registry = NoteTypeRegistry()

        register_builtin_note_types(registry)

        for alias in ("biopsychosocial", "stanley_brown", "medication_list"):
            assert not registry.has(alias)

    def test_patient_context_definitions_are_patient_scoped(self) -> None:
        assert INTAKE_DEFINITION.context == "patient"
        assert TREATMENT_PLAN_DEFINITION.context == "patient"
        assert SAFETY_PLAN_DEFINITION.context == "patient"
        assert MEDICATIONS_DEFINITION.context == "patient"

    def test_session_context_definitions_unchanged(self) -> None:
        assert SOAP_DEFINITION.context == "session"
        assert NARRATIVE_DEFINITION.context == "session"

    def test_bundler_canonical_keys_all_resolve_in_registry(self) -> None:
        """Each note-type set the chat context bundler names must have at
        least one key registered, so the bundler and the registry cannot
        silently drift apart."""
        registry = NoteTypeRegistry()
        register_builtin_note_types(registry)

        for note_type_set in (
            INTAKE_NOTE_TYPES,
            TREATMENT_PLAN_NOTE_TYPES,
            SAFETY_PLAN_NOTE_TYPES,
            MEDICATIONS_NOTE_TYPES,
        ):
            assert any(registry.has(key) for key in note_type_set), (
                f"none of {note_type_set} are registered"
            )

    def test_new_definitions_have_sections_and_fields_with_unique_keys(self) -> None:
        for definition in (
            INTAKE_DEFINITION,
            TREATMENT_PLAN_DEFINITION,
            SAFETY_PLAN_DEFINITION,
            MEDICATIONS_DEFINITION,
        ):
            assert definition.sections
            section_keys = definition.section_keys()
            assert len(section_keys) == len(set(section_keys))
            for section in definition.sections:
                assert section.fields

    def test_new_definitions_do_not_set_prompt_builder(self) -> None:
        for definition in (
            INTAKE_DEFINITION,
            TREATMENT_PLAN_DEFINITION,
            SAFETY_PLAN_DEFINITION,
            MEDICATIONS_DEFINITION,
        ):
            assert definition.prompt_builder is None

    def test_narrative_is_single_text_field(self) -> None:
        assert NARRATIVE_DEFINITION.tier == "core"
        assert NARRATIVE_DEFINITION.context == "session"
        assert NARRATIVE_DEFINITION.section_keys() == ["note"]
        [section] = NARRATIVE_DEFINITION.sections
        [field] = section.fields
        assert field.kind == "text"

    def test_soap_sections_mirror_soapnote_dataclass(self) -> None:
        """SOAP registry must line up with the SOAPNote dataclass so the
        upcoming generation refactor stays behavior-preserving."""
        assert SOAP_DEFINITION.tier == "core"
        assert SOAP_DEFINITION.context == "session"
        assert SOAP_DEFINITION.section_keys() == [
            "subjective",
            "objective",
            "assessment",
            "plan",
        ]

        section_to_dataclass = {
            "subjective": SubjectiveNote,
            "objective": ObjectiveNote,
            "assessment": AssessmentNote,
            "plan": PlanNote,
        }
        for section in SOAP_DEFINITION.sections:
            dc = section_to_dataclass[section.key]
            dc_field_names = {f.name for f in dataclasses.fields(dc)}
            assert set(section.field_keys()) == dc_field_names, (
                f"SOAP section {section.key} fields "
                f"{section.field_keys()} drifted from {dc.__name__} "
                f"fields {sorted(dc_field_names)}"
            )

    def test_soap_list_fields_are_list_kind(self) -> None:
        """The SOAP fields that are list[SOAPSentence] on the dataclass must
        be ``kind='list'`` on the registry."""
        expected_list_fields = {
            ("subjective", "symptoms"),
            ("plan", "interventions_used"),
            ("plan", "homework_assignments"),
            ("plan", "next_steps"),
        }
        found: set[tuple[str, str]] = set()
        for section in SOAP_DEFINITION.sections:
            for f in section.fields:
                if f.kind == "list":
                    found.add((section.key, f.key))
        assert found == expected_list_fields

    def test_every_field_has_nonempty_label(self) -> None:
        for definition in ALL_BUILTIN_DEFINITIONS:
            for section in definition.sections:
                assert section.label
                for f in section.fields:
                    assert f.label, f"{definition.key}.{section.key}.{f.key} missing label"

    def test_builtins_do_not_set_system_prompt(self) -> None:
        for definition in ALL_BUILTIN_DEFINITIONS:
            assert definition.system_prompt is None


class TestSystemPrompt:
    def test_defaults_to_none(self) -> None:
        assert _tiny_type().system_prompt is None

    def test_can_be_set(self) -> None:
        prompt = "You are a clinical supervisor summarizing a case review."
        definition = NoteTypeDefinition(
            key="review",
            label="Case Review",
            description="For tests.",
            sections=(),
            system_prompt=prompt,
        )
        assert definition.system_prompt == prompt


class TestDefaultRegistry:
    def test_default_registry_is_singleton(self) -> None:
        assert get_default_registry() is get_default_registry()

    def test_default_registry_has_builtins_after_app_import(self) -> None:
        """Importing app.main (done via conftest) must have registered the
        OSS built-ins on the default registry."""
        registry = get_default_registry()
        for definition in ALL_BUILTIN_DEFINITIONS:
            assert registry.has(definition.key)
