# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a new client's name fields start with, and what a chart is called without them."""

from __future__ import annotations

import pytest
from app.models.patient import Patient, PatientResponse
from app.patients.new_client_name import SuggestedName, chart_name, name_part, suggested_name
from app.patients.titles import NOT_NAME_WORDS, SESSION_WORDS
from app.utcnow import utc_now


class TestSuggestedName:
    @pytest.mark.parametrize(
        "title",
        [
            "Casey Morgan",
            "Session with Casey Morgan",
            "Therapy session with Casey Morgan",
            "Morgan, Casey",
            "Casey Morgan - Therapy",
            "Intake: Casey Morgan",
            "Call with Casey Morgan",
            "Video call - Casey Morgan",
        ],
    )
    def test_a_clear_full_name_fills_in_both(self, title: str) -> None:
        assert suggested_name(title) == SuggestedName("Casey", "Morgan")

    def test_every_word_that_books_is_also_kept_out_of_a_name(self) -> None:
        booking_words = {word for phrase in SESSION_WORDS for word in phrase.split()}
        assert booking_words <= NOT_NAME_WORDS

    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Jane S.", SuggestedName("Jane", "")),
            ("Session with Jane S.", SuggestedName("Jane", "")),
            ("J. Smith", SuggestedName("", "Smith")),
            ("Session with J. Smith", SuggestedName("", "Smith")),
        ],
    )
    def test_a_name_cut_short_fills_in_only_the_whole_part(
        self, title: str, expected: SuggestedName
    ) -> None:
        assert suggested_name(title) == expected

    @pytest.mark.parametrize("title", ["Jane", "Session with Jane"])
    def test_one_word_fills_in_nothing(self, title: str) -> None:
        # "Jane" could be a first or a last name.
        assert suggested_name(title) is None

    @pytest.mark.parametrize("title", ["K.M.", "KM", "K. M.", "Session with K.M."])
    def test_initials_fill_in_nothing(self, title: str) -> None:
        assert suggested_name(title) is None

    @pytest.mark.parametrize(
        "title",
        [
            "Therapy Session",
            "Intake appointment",
            "Video call",
            "Jane Smith - Bob Jones",
            "Mary Ann Smith",
            "Casey 2nd",
        ],
    )
    def test_session_wording_or_anything_unclear_fills_in_nothing(self, title: str) -> None:
        assert suggested_name(title) is None


class TestNamePart:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Session with K.M.", "K.M."),
            ("K.M.", "K.M."),
            ("Therapy session with Jane S.", "Jane S."),
            ("Jane - Telehealth", "Jane"),
            ("Casey Morgan (video)", "Casey Morgan"),
            ("Therapy Session", ""),
        ],
    )
    def test_session_wording_is_taken_out(self, title: str, expected: str) -> None:
        assert name_part(title) == expected


class TestChartName:
    def test_names_are_saved_as_typed(self) -> None:
        assert chart_name("  Kim ", " Moreau", "K.M.", unnamed="New client") == ("Kim", "Moreau")

    def test_one_typed_part_is_kept_without_the_title(self) -> None:
        assert chart_name("Jane", "", "Jane S.", unnamed="New client") == ("Jane", "")

    def test_nothing_typed_gives_the_name_part_and_no_last_name(self) -> None:
        assert chart_name(None, "  ", "Session with K.M.", unnamed="New client") == ("K.M.", "")

    def test_nothing_left_gives_the_unnamed_label(self) -> None:
        assert chart_name(None, None, "Therapy Session", unnamed="New client") == (
            "New client",
            "",
        )

    def test_long_names_are_cut_to_the_column(self) -> None:
        first, last = chart_name("a" * 300, "b" * 300, None, unnamed="New client")
        assert (len(first), len(last)) == (255, 255)


class TestNeedsName:
    @staticmethod
    def _patient(first: str, last: str) -> Patient:
        now = utc_now()
        return Patient(id="p1", first_name=first, last_name=last, created_at=now, updated_at=now)

    @pytest.mark.parametrize(
        ("first", "last", "needs"),
        [("Kim", "Moreau", False), ("K.M.", "", True), ("", "Smith", True), ("Jane", " ", True)],
    )
    def test_a_chart_without_a_first_or_last_name_needs_one(
        self, first: str, last: str, needs: bool
    ) -> None:
        patient = self._patient(first, last)
        assert patient.needs_name is needs
        assert PatientResponse.from_patient(patient).needs_name is needs
