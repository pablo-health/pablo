# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an exported form says, and how it says it.

The renderer on its own: no database, no routes, no HTTP. What is under
test is the half of the export that decides what an answer reads as and
what the file looks like — the half a route test can only reach through a
form somebody filled in, and so can only reach a few shapes of.

The escaping tests are the ones worth keeping honest. They assert against
the produced markup rather than against a helper that claims to escape, so
a value that reached the page unescaped fails here whatever the helper
says it did.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from app.intake.export import (
    ExportAnswer,
    ExportArtifact,
    ExportEvent,
    ExportItem,
    ExportSignature,
    IntakeExport,
    answer_lines,
    item_heading,
    render,
)
from app.intake.items import ItemConfig, validate_item_config

_WHEN = datetime(2026, 3, 14, 9, 30, tzinfo=UTC)
_TAKEN = datetime(2026, 3, 14, 17, 5, tzinfo=UTC)
_NEW_YORK = ZoneInfo("America/New_York")
_SCRIPT = "<script>alert('x')</script>"


def _config(item_type: str, **config: object) -> ItemConfig:
    return validate_item_config(item_type, config)


def _export(**overrides: object) -> IntakeExport:
    """A form with one plain question, for tests that vary one thing."""
    base: dict[str, object] = {
        "practice_name": "Bramble Street Counseling",
        "patient_name": "Ada Lovelace",
        "patient_date_of_birth": "1990-03-14",
        "packet_name": "Intake",
        "version": 1,
        "status": "submitted",
        "receipt_code": "K7M2QP4T",
        "assigned_at": _WHEN,
        "submitted_at": _WHEN,
        "accepted_at": None,
        "withdrawn_at": None,
        "exported_at": _TAKEN,
        "timezone": UTC,
        "items": [],
        "artifacts": [],
        "signatures": [],
        "events": [],
    }
    return IntakeExport(**{**base, **overrides})  # type: ignore[arg-type] — a fixture builder


def _artifact(**overrides: object) -> ExportArtifact:
    base: dict[str, object] = {
        "heading": "A photo of your insurance card",
        "filename": "card-front.png",
        "side": "front",
        "size_bytes": 2048,
        "url": "https://pablo.example/api/documents/doc-1/file",
    }
    return ExportArtifact(**{**base, **overrides})  # type: ignore[arg-type] — a fixture builder


def _item(**overrides: object) -> ExportItem:
    base: dict[str, object] = {
        "key": "goals",
        "item_type": "free_text",
        "heading": "What would you like to be different?",
        "help_text": None,
        "body_html": None,
        "shown": True,
        "required": True,
        "current": None,
        "history": [],
    }
    return ExportItem(**{**base, **overrides})  # type: ignore[arg-type] — a fixture builder


def _event(note: str) -> ExportEvent:
    return ExportEvent(kind="accepted", created_at=_WHEN, note_to_patient=note)


def _answer(lines: list[str], state: str = "current") -> ExportAnswer:
    return ExportAnswer(lines=lines, state=state, provenance="patient", written_at=_WHEN)  # type: ignore[arg-type] — a fixture builder


# ---------------------------------------------------------------------------
# What an answer reads as
# ---------------------------------------------------------------------------


class TestAnswerLines:
    def test_nothing_stored_reads_as_nothing(self) -> None:
        assert answer_lines(_config("free_text"), None) == []
        assert answer_lines(_config("free_text"), {}) == []

    def test_a_written_answer_keeps_its_line_breaks(self) -> None:
        config = _config("free_text", max_len=500)
        assert answer_lines(config, {"text": "Two things.\nThe second one."}) == [
            "Two things.",
            "The second one.",
        ]

    def test_a_choice_reads_as_the_wording_the_patient_read(self) -> None:
        config = _config(
            "single_choice",
            options=[{"key": "a", "label": "Most days"}, {"key": "b", "label": "Now and then"}],
        )
        assert answer_lines(config, {"key": "b"}) == ["Now and then"]

    def test_a_choice_whose_wording_is_gone_reads_as_its_stored_name(self) -> None:
        """A key outlives a relabelling, and is more use than nothing."""
        config = _config(
            "single_choice",
            options=[{"key": "a", "label": "Most days"}, {"key": "b", "label": "Now and then"}],
        )
        assert answer_lines(config, {"key": "gone"}) == ["gone"]

    def test_picked_answers_read_in_the_order_the_question_offers_them(self) -> None:
        """Click order is not a fact about the answer, and two copies must agree."""
        config = _config(
            "multi_choice",
            options=[
                {"key": "sleep", "label": "Sleep"},
                {"key": "mood", "label": "Mood"},
                {"key": "work", "label": "Work"},
            ],
        )
        assert answer_lines(config, {"keys": ["work", "sleep"]}) == ["Sleep", "Work"]

    def test_a_yes_carries_the_box_it_opened(self) -> None:
        config = _config("yes_no", follow_up_label="What happened?")
        assert answer_lines(config, {"yes": True, "follow_up": "A bad week."}) == [
            "Yes",
            "A bad week.",
        ]

    def test_a_no_leaves_a_stale_follow_up_behind(self) -> None:
        config = _config("yes_no", follow_up_label="What happened?")
        assert answer_lines(config, {"yes": False, "follow_up": "A bad week."}) == ["No"]

    def test_a_scale_carries_the_words_at_each_end(self) -> None:
        config = _config("scale", min=0, max=10, min_label="Not at all", max_label="Constantly")
        assert answer_lines(config, {"value": 7}) == ["7 (0 is Not at all, 10 is Constantly)"]

    def test_a_number_carries_its_unit(self) -> None:
        assert answer_lines(_config("number", unit="hours"), {"value": 5}) == ["5 hours"]

    def test_confirming_the_chart_reads_as_the_attestation_it_is(self) -> None:
        answer = answer_lines(
            _config("demographics"),
            {"name_confirmed": True, "dob_confirmed": False, "corrections": "Born in June."},
        )
        assert answer == [
            "Name on record: confirmed",
            "Date of birth on record: flagged as wrong",
            "Correction: Born in June.",
        ]

    def test_a_measure_carries_its_total_and_band(self) -> None:
        config = _config("instrument", code="phq9")
        lines = answer_lines(config, {"item_scores": {str(i): 2 for i in range(1, 10)}})
        assert lines[0] == "PHQ-9: 18 of 27 (moderately severe)"
        assert lines[1].startswith("1: 2, 2: 2")

    def test_a_measure_missing_an_item_has_no_total(self) -> None:
        """A total over some of the items is not this measure's score."""
        config = _config("instrument", code="phq9")
        lines = answer_lines(config, {"item_scores": {str(i): 2 for i in range(1, 9)}})
        assert lines[0] == "PHQ-9: not every question was answered."
        assert "18" not in lines[0]

    def test_a_question_that_no_longer_parses_still_prints_its_answer(self) -> None:
        """Losing the question is bad enough without also losing the answer."""
        assert answer_lines(None, {"text": "Panic.", "note": "a"}) == ["note: a", "text: Panic."]

    def test_a_card_reads_as_the_sides_that_arrived(self) -> None:
        """In the order a card has them, not the order they were sent."""
        config = _config("insurance_card", sides="both")
        value = {
            "documents": [
                {"document_id": "doc-back", "side": "back"},
                {"document_id": "doc-front", "side": "front"},
            ]
        }
        assert answer_lines(config, value) == [
            "Front of the card sent.",
            "Back of the card sent.",
        ]

    def test_a_card_never_prints_a_document_id(self) -> None:
        """An id is a handle into one deployment's storage and travels badly."""
        config = _config("insurance_card", sides="front")
        value = {"documents": [{"document_id": "doc-front", "side": "front"}]}
        assert "doc-front" not in " ".join(answer_lines(config, value))

    def test_a_document_request_reads_as_how_many_arrived(self) -> None:
        config = _config("document_request")
        one = {"documents": [{"document_id": "a"}]}
        two = {"documents": [{"document_id": "a"}, {"document_id": "b"}]}
        assert answer_lines(config, one) == ["1 file sent."]
        assert answer_lines(config, two) == ["2 files sent."]

    def test_a_file_question_with_nothing_on_it_reads_as_unanswered(self) -> None:
        config = _config("document_request")
        assert answer_lines(config, {"documents": []}) == []
        assert answer_lines(_config("insurance_card"), {"documents": []}) == []

    def test_a_file_answer_that_is_not_the_stored_shape_names_nothing(self) -> None:
        """JSONB holds whatever was written; an unreadable value claims nothing."""
        config = _config("document_request")
        assert answer_lines(config, {"documents": "one"}) == []


class TestItemHeading:
    def test_the_practices_own_wording_wins(self) -> None:
        assert item_heading(_config("free_text"), "Why now?", "why", "free_text") == "Why now?"

    def test_a_section_is_called_by_its_title(self) -> None:
        config = _config("section", title="About you")
        assert item_heading(config, None, "about", "section") == "About you"

    def test_a_measure_is_called_by_its_name(self) -> None:
        assert item_heading(_config("instrument", code="gad7"), None, "g", "instrument") == "GAD-7"

    def test_an_engine_question_is_called_what_the_builder_calls_it(self) -> None:
        assert item_heading(_config("reason"), None, "reason", "reason") == "What brings you in"

    def test_anything_else_falls_back_to_the_questions_own_name(self) -> None:
        assert item_heading(None, None, "custom_thing", "free_text") == "custom_thing"


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


class TestRender:
    def test_it_is_one_self_contained_page(self) -> None:
        page = render(_export())
        assert page.startswith("<!DOCTYPE html>")
        assert "<script" not in page.lower()
        assert "src=" not in page
        assert "K7M2QP4T" in page
        assert "2026-03-14 09:30 UTC" in page

    def test_the_same_form_renders_the_same_bytes(self) -> None:
        assert render(_export()) == render(_export())

    def test_it_says_when_this_copy_was_taken(self) -> None:
        """A chart copy with no date on it is a document nobody can place."""
        page = render(_export())
        assert "Exported on 2026-03-14 17:05 (UTC)" in page

    def test_two_copies_a_minute_apart_differ_in_that_line_and_nowhere_else(self) -> None:
        first = render(_export()).splitlines()
        later = render(_export(exported_at=_TAKEN + timedelta(minutes=1))).splitlines()
        differing = [(a, b) for a, b in zip(first, later, strict=True) if a != b]
        assert len(first) == len(later)
        assert len(differing) == 1
        assert "Exported on" in differing[0][0]

    def test_moments_are_written_in_the_practices_own_day(self) -> None:
        """A clinician converting UTC in their head is doing arithmetic on
        their own record."""
        page = render(_export(timezone=_NEW_YORK))
        assert "Exported on 2026-03-14 13:05 (America/New_York)" in page
        # 09:30 UTC is 05:30 in New York on that date, and the page names
        # the zone so the copy is unambiguous wherever it is forwarded.
        assert "2026-03-14 05:30 EDT" in page
        assert "09:30 UTC" not in page

    def test_a_practice_that_has_not_named_itself_gets_no_line(self) -> None:
        """Nothing here invents a name, and a blank line is not information."""
        assert "<header>\n<h1>" in render(_export(practice_name=None))

    @pytest.mark.parametrize(
        "export",
        [
            _export(patient_name=_SCRIPT),
            _export(packet_name=_SCRIPT),
            _export(receipt_code=_SCRIPT),
            _export(items=[_item(heading=_SCRIPT)]),
            _export(items=[_item(help_text=_SCRIPT)]),
            _export(items=[_item(current=_answer([_SCRIPT]))]),
            _export(events=[_event(_SCRIPT)]),
            _export(artifacts=[_artifact(filename=f"{_SCRIPT}.png")]),
            _export(artifacts=[_artifact(heading=_SCRIPT)]),
        ],
        ids=[
            "patient",
            "packet",
            "receipt",
            "heading",
            "help",
            "answer",
            "note",
            "filename",
            "artifact-heading",
        ],
    )
    def test_no_value_reaches_the_page_as_markup(self, export: IntakeExport) -> None:
        page = render(export)
        assert _SCRIPT not in page
        assert "&lt;script&gt;" in page

    def test_a_question_nobody_was_shown_says_so_rather_than_reading_as_blank(self) -> None:
        page = render(_export(items=[_item(shown=False)]))
        assert "Not asked." in page
        assert "No answer." not in page

    def test_a_question_that_was_shown_and_skipped_says_that_instead(self) -> None:
        assert "No answer." in render(_export(items=[_item()]))

    def test_a_file_question_nobody_was_shown_says_so_too(self) -> None:
        """The rule is about the question, not about what kind it is."""
        page = render(_export(items=[_item(item_type="insurance_card", shown=False)]))
        assert "Not asked." in page
        assert "No answer." not in page

    def test_an_earlier_answer_says_what_became_of_it(self) -> None:
        page = render(
            _export(
                items=[
                    _item(
                        current=_answer(["Sleeping better."]),
                        history=[
                            _answer(["Not sleeping."], state="replaced"),
                            _answer(["Something else."], state="withheld"),
                        ],
                    )
                ]
            )
        )
        assert "Replaced · Answered by the patient" in page
        assert "Withheld — the form stopped asking this" in page

    def test_a_form_with_no_signature_and_no_history_has_neither_heading(self) -> None:
        page = render(_export())
        assert "Signatures" not in page
        assert "History" not in page
        assert "Attached files" not in page

    def test_an_attached_file_is_named_and_linked_rather_than_embedded(self) -> None:
        """A photograph printed into a copy that gets forwarded cannot be
        un-sent."""
        page = render(_export(artifacts=[_artifact()]))
        assert "Attached files" in page
        assert "A photo of your insurance card" in page
        assert "card-front.png (front)" in page
        assert "2.0 KB" in page
        assert 'href="https://pablo.example/api/documents/doc-1/file"' in page
        assert "<img" not in page
        assert "data:image" not in page

    def test_a_file_that_answers_no_side_is_named_without_one(self) -> None:
        page = render(_export(artifacts=[_artifact(side=None, filename="records.pdf")]))
        assert "records.pdf ·" in page
        assert "records.pdf (" not in page

    @pytest.mark.parametrize(
        ("size_bytes", "written"),
        [(1, "1 bytes"), (900, "900 bytes"), (2048, "2.0 KB"), (5_242_880, "5.0 MB")],
    )
    def test_a_size_is_written_in_the_unit_a_person_would_say_it_in(
        self, size_bytes: int, written: str
    ) -> None:
        page = render(_export(artifacts=[_artifact(size_bytes=size_bytes)]))
        assert written in page

    def test_a_signature_prints_the_evidence_that_makes_it_checkable(self) -> None:
        page = render(
            _export(
                signatures=[
                    ExportSignature(
                        document_title="Consent to treatment",
                        document_version=2,
                        document_version_id="v-1",
                        document_digest="d" * 64,
                        signer_role="guardian",
                        signer_typed_name="Ada Lovelace",
                        consent_statement="By typing my name…",
                        consent_statement_version="1",
                        signed_at=_WHEN,
                        auth_strength="stepped_up",
                        session_id="s-1",
                        ip="203.0.113.4",
                        user_agent="Mozilla/5.0",
                        evidence_digest="e" * 64,
                    )
                ]
            )
        )
        assert "Consent to treatment v2" in page
        assert "Ada Lovelace (guardian)" in page
        assert "203.0.113.4" in page
        assert "e" * 64 in page

    def test_it_asks_the_printer_not_to_split_a_question(self) -> None:
        assert "break-inside: avoid" in render(_export())
