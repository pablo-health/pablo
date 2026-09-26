# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""RecordSetSelector: the include rules for a copy of the chart."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models import Note, PatientDocument, TherapySession, Transcript
from app.models.export import ExportOptions
from app.models.patient_document import DocumentCategory
from app.services.record_set import RecordSetSelector

_T0 = datetime(2024, 1, 15, tzinfo=UTC)

_DEFAULT = RecordSetSelector()
_EVERYTHING = RecordSetSelector(include_transcripts=True, include_psychotherapy_notes=True)


def _note(*, restricted: bool) -> Note:
    return Note(
        id="n",
        patient_id="p",
        note_type="psychotherapy" if restricted else "narrative",
        created_at=_T0,
        updated_at=_T0,
        restricted=restricted,
    )


def _document(category: DocumentCategory) -> PatientDocument:
    return PatientDocument(
        id="d",
        patient_id="p",
        user_id="u",
        filename="letter.pdf",
        mime_type="application/pdf",
        gcs_path="k",
        size_bytes=1,
        created_at=_T0,
        category=category,
    )


def test_options_echo_the_choices() -> None:
    assert _DEFAULT.options == ExportOptions()
    assert _EVERYTHING.options == ExportOptions(
        include_transcripts=True, include_psychotherapy_notes=True
    )


def test_a_psychotherapy_note_needs_its_option() -> None:
    assert _DEFAULT.includes_note(_note(restricted=False))
    assert not _DEFAULT.includes_note(_note(restricted=True))
    assert _EVERYTHING.includes_note(_note(restricted=True))


def test_a_transcript_needs_its_option() -> None:
    transcript = Transcript(format="txt", content="hello")
    session = TherapySession(
        id="s",
        user_id="u",
        patient_id="p",
        session_date=_T0,
        session_number=1,
        status="finalized",
        transcript=transcript,
        created_at=_T0,
    )
    assert _DEFAULT.transcript_for(session) is None
    assert _EVERYTHING.transcript_for(session) is transcript


@pytest.mark.parametrize(
    ("category", "by_default", "with_psychotherapy_notes"),
    [
        (DocumentCategory.CHART, True, True),
        (DocumentCategory.PSYCHOTHERAPY_NOTES, False, True),
        (DocumentCategory.THERAPIST_PRIVATE, False, False),
    ],
)
def test_document_rules(
    category: DocumentCategory, by_default: bool, with_psychotherapy_notes: bool
) -> None:
    document = _document(category)
    assert _DEFAULT.includes_document(document) is by_default
    assert _EVERYTHING.includes_document(document) is with_psychotherapy_notes


def test_every_other_document_category_is_part_of_the_record() -> None:
    held_back = {DocumentCategory.PSYCHOTHERAPY_NOTES, DocumentCategory.THERAPIST_PRIVATE}
    for category in set(DocumentCategory) - held_back:
        assert _DEFAULT.includes_document(_document(category)), category
