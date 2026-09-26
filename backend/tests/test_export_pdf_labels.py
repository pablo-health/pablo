# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note fields in the exported PDF read as the note type labels them."""

from __future__ import annotations

from app.notes import NARRATIVE_DEFINITION, SOAP_DEFINITION
from app.services.export_pdf import note_paragraphs


def test_fields_take_the_note_types_labels_in_its_order() -> None:
    content = {
        "plan": {"next_steps": "Revisit in two weeks"},
        "subjective": {"client_narrative": "A long week", "symptoms": ["poor sleep", ""]},
    }
    assert note_paragraphs(content, SOAP_DEFINITION) == [
        ("Subjective - Symptoms", "poor sleep"),
        ("Subjective - Client Narrative", "A long week"),
        ("Plan", "Revisit in two weeks"),
    ]


def test_a_single_field_section_prints_under_the_section_label() -> None:
    content = {"note": {"body": "Called to reschedule."}}
    assert note_paragraphs(content, NARRATIVE_DEFINITION) == [("Note", "Called to reschedule.")]


def test_what_the_definition_does_not_describe_still_prints() -> None:
    content = {
        "subjective": {"client_narrative": "A long week", "retired_field": "kept"},
        "addendum": "Written after the session",
    }
    assert note_paragraphs(content, SOAP_DEFINITION) == [
        ("Subjective - Client Narrative", "A long week"),
        ("Subjective - Retired field", "kept"),
        ("Addendum", "Written after the session"),
    ]


def test_an_unregistered_type_is_labelled_from_its_keys() -> None:
    content = {"session_summary": {"what_happened": "x", "next_time": "y"}}
    assert note_paragraphs(content, None) == [
        ("Session summary - What happened", "x"),
        ("Session summary - Next time", "y"),
    ]
