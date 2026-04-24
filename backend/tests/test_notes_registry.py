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
    NARRATIVE_DEFINITION,
    SOAP_DEFINITION,
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
    get_default_registry,
    register_builtin_note_types,
)


def _tiny_type(key: str = "tiny") -> NoteTypeDefinition:
    return NoteTypeDefinition(
        key=key,
        label="Tiny",
        description="For tests.",
        tier="oss",
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
    def test_register_builtin_populates_soap_and_narrative(self) -> None:
        registry = NoteTypeRegistry()

        register_builtin_note_types(registry)

        assert registry.keys() == ["narrative", "soap"]
        assert registry.get("soap") is SOAP_DEFINITION
        assert registry.get("narrative") is NARRATIVE_DEFINITION

    def test_register_builtin_is_idempotent(self) -> None:
        registry = NoteTypeRegistry()

        register_builtin_note_types(registry)
        register_builtin_note_types(registry)

        assert registry.keys() == ["narrative", "soap"]

    def test_narrative_is_single_text_field(self) -> None:
        assert NARRATIVE_DEFINITION.tier == "oss"
        assert NARRATIVE_DEFINITION.section_keys() == ["note"]
        [section] = NARRATIVE_DEFINITION.sections
        [field] = section.fields
        assert field.kind == "text"

    def test_soap_sections_mirror_soapnote_dataclass(self) -> None:
        """SOAP registry must line up with the SOAPNote dataclass so the
        upcoming generation refactor stays behavior-preserving."""
        assert SOAP_DEFINITION.tier == "oss"
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
        for definition in (SOAP_DEFINITION, NARRATIVE_DEFINITION):
            for section in definition.sections:
                assert section.label
                for f in section.fields:
                    assert f.label, f"{definition.key}.{section.key}.{f.key} missing label"


class TestDefaultRegistry:
    def test_default_registry_is_singleton(self) -> None:
        assert get_default_registry() is get_default_registry()

    def test_default_registry_has_builtins_after_app_import(self) -> None:
        """Importing app.main (done via conftest) must have registered the
        OSS built-ins on the default registry."""
        registry = get_default_registry()
        assert registry.has("soap")
        assert registry.has("narrative")
