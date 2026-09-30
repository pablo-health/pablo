# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the one patient matcher every import and feed shares."""

from __future__ import annotations

from datetime import date

import pytest
from app.models.patient import Patient
from app.patients.matching import (
    Candidate,
    MatchContext,
    PatientHint,
    match_patient,
    remember_match,
    remember_not_a_client,
)
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import InMemoryPatientSourceMappingRepository
from app.utcnow import utc_now

USER = "clinician-1"


def _patient(
    patient_id: str,
    first: str,
    last: str,
    *,
    email: str | None = None,
    dob: str | None = None,
) -> Patient:
    now = utc_now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        email=email,
        date_of_birth=dob,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def patients() -> InMemoryPatientRepository:
    return InMemoryPatientRepository()


@pytest.fixture
def mappings() -> InMemoryPatientSourceMappingRepository:
    return InMemoryPatientSourceMappingRepository()


def _ctx(
    patients: InMemoryPatientRepository,
    mappings: InMemoryPatientSourceMappingRepository,
    *rows: Patient,
) -> MatchContext:
    for row in rows:
        patients.create(row, USER)
    return MatchContext.for_practice(USER, patients, mappings)


class TestOrder:
    def test_name_and_dob_decide_before_a_shared_family_email(self, patients, mappings) -> None:
        """A child's record carrying the family email lands on the child's chart."""
        ctx = _ctx(
            patients,
            mappings,
            _patient("parent", "Pat", "Lee", email="family@example.com", dob="1975-04-01"),
            _patient("child", "Sam", "Lee", dob="2012-06-09"),
        )
        hint = PatientHint(
            full_name="Sam Lee", email="family@example.com", date_of_birth=date(2012, 6, 9)
        )
        result = match_patient(hint, ctx)
        assert (result.patient_id, result.evidence) == ("child", "name_and_dob")

    def test_email_decides_before_the_name(self, patients, mappings) -> None:
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", "Jane", "Adams"),
            _patient("p2", "Janet", "Adams", email="jane@example.com"),
        )
        result = match_patient(PatientHint(full_name="Jane Adams", email="jane@example.com"), ctx)
        assert (result.patient_id, result.evidence) == ("p2", "email")

    def test_a_date_of_birth_separates_two_patients_with_one_name(self, patients, mappings) -> None:
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", "Jane", "Adams", dob="1980-01-02"),
            _patient("p2", "Jane", "Adams", dob="1991-05-06"),
        )
        hint = PatientHint(full_name="Jane Adams", date_of_birth=date(1991, 5, 6))
        result = match_patient(hint, ctx)
        assert (result.patient_id, result.evidence) == ("p2", "name_and_dob")

    def test_a_unique_full_name_matches(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "Bo", "Li"))
        result = match_patient(PatientHint(full_name="Jane Adams"), ctx)
        assert (result.patient_id, result.evidence) == ("p1", "full_name")

    def test_unique_initials_match(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "Bo", "Li"))
        result = match_patient(PatientHint(initials="J.A."), ctx)
        assert (result.patient_id, result.evidence) == ("p1", "initials")

    def test_an_ambiguous_email_falls_through_to_the_name(self, patients, mappings) -> None:
        """A shared family email names nobody; the name still can."""
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", "Jane", "Adams", email="family@example.com"),
            _patient("p2", "John", "Adams", email="family@example.com"),
        )
        result = match_patient(PatientHint(full_name="John Adams", email="family@example.com"), ctx)
        assert (result.patient_id, result.evidence) == ("p2", "full_name")


class TestUniqueness:
    def test_two_patients_with_one_name_are_a_question_not_a_match(
        self, patients, mappings
    ) -> None:
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", "Jane", "Adams"),
            _patient("p2", "Jane", "Adams"),
        )
        result = match_patient(PatientHint(full_name="Jane Adams"), ctx)
        assert result.patient_id is None
        assert result.evidence is None
        assert sorted(result.possible_ids) == ["p1", "p2"]

    def test_shared_initials_are_a_question(self, patients, mappings) -> None:
        ctx = _ctx(
            patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "John", "Avery")
        )
        result = match_patient(PatientHint(initials="J.A."), ctx)
        assert result.patient_id is None
        assert sorted(result.possible_ids) == ["p1", "p2"]

    def test_nobody_matching_is_an_empty_answer(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        result = match_patient(PatientHint(full_name="Robin Tran"), ctx)
        assert result.patient_id is None
        assert result.possible_ids == []

    def test_a_single_word_is_not_a_full_name(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        assert match_patient(PatientHint(full_name="Jane"), ctx).patient_id is None

    def test_a_name_alone_can_be_made_a_question(self, patients, mappings) -> None:
        """Callers that merge records ask rather than merge on a name."""
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        result = match_patient(PatientHint(full_name="Jane Adams"), ctx, name_alone_is_enough=False)
        assert result.patient_id is None
        assert result.possible_ids == ["p1"]


class TestNormalization:
    def test_case_and_whitespace_do_not_matter(self, patients, mappings) -> None:
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", " Jane ", "ADAMS", email="Jane@Example.com "),
            _patient("p2", "Bo", "Li"),
        )
        assert match_patient(PatientHint(full_name="  jane   adams "), ctx).patient_id == "p1"
        assert match_patient(PatientHint(email=" JANE@example.COM"), ctx).patient_id == "p1"
        assert match_patient(PatientHint(initials="j.a."), ctx).patient_id == "p1"

    def test_a_name_agreeing_only_on_first_and_last_word_is_only_possible(
        self, patients, mappings
    ) -> None:
        """Mary Ann Smith may not be the Mary Smith already on file."""
        ctx = _ctx(patients, mappings, _patient("p1", "Mary", "Smith"))
        result = match_patient(PatientHint(full_name="Mary Ann Smith"), ctx)
        assert result.patient_id is None
        assert result.possible_ids == ["p1"]

    def test_a_whole_name_match_beside_a_partial_one_is_only_possible(
        self, patients, mappings
    ) -> None:
        ctx = _ctx(
            patients,
            mappings,
            _patient("p1", "Mary Ann", "Smith"),
            _patient("p2", "Mary", "Smith"),
        )
        result = match_patient(PatientHint(full_name="Mary Ann Smith"), ctx)
        assert result.patient_id is None
        assert sorted(result.possible_ids) == ["p1", "p2"]

    def test_a_two_word_first_name_matches_as_a_whole(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Mary Ann", "Smith"))
        assert match_patient(PatientHint(full_name="Mary Ann Smith"), ctx).patient_id == "p1"

    def test_a_chart_named_from_a_calendar_title_matches_that_title(
        self, patients, mappings
    ) -> None:
        """A calendar import puts the whole title in the first name."""
        ctx = _ctx(patients, mappings, _patient("p1", "Jane Adams", ""))
        assert match_patient(PatientHint(full_name="jane adams"), ctx).patient_id == "p1"


class TestRemembered:
    def test_a_remembered_answer_is_reused(self, patients, mappings) -> None:
        ctx = _ctx(
            patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "Jane", "Adams")
        )
        remember_match("google_calendar", "series:abc", "p2", ctx)

        fresh = MatchContext.for_practice(USER, patients, mappings)
        hint = PatientHint(
            full_name="Jane Adams", source="google_calendar", source_identifier="series:abc"
        )
        result = match_patient(hint, fresh)
        assert (result.patient_id, result.evidence) == ("p2", "remembered")

    def test_remembering_twice_stores_one_answer(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        remember_match("simplepractice", "J.A.", "p1", ctx)
        remember_match("simplepractice", " j.a. ", "p1", ctx)
        assert len(mappings.list_by_source(USER, "simplepractice")) == 1

    def test_remembering_a_different_patient_replaces_the_answer(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "Bo", "Li"))
        remember_match("simplepractice", "J.A.", "p1", ctx)
        remember_match("simplepractice", "j.a.", "p2", ctx)
        stored = mappings.list_by_source(USER, "simplepractice")
        assert [(m.source_identifier, m.patient_id) for m in stored] == [("J.A.", "p2")]

    def test_an_answer_for_one_source_does_not_leak_into_another(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"), _patient("p2", "Bo", "Li"))
        remember_match("simplepractice", "SH00001", "p1", ctx)
        hint = PatientHint(source="sessions_health", source_identifier="SH00001")
        assert match_patient(hint, ctx).patient_id is None

    def test_another_clinicians_answer_is_not_used(self, patients, mappings) -> None:
        mine = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        remember_match("simplepractice", "J.A.", "p1", mine)
        patients.grant_access("p1", "clinician-2")
        theirs = MatchContext.for_practice("clinician-2", patients, mappings)
        hint = PatientHint(source="simplepractice", source_identifier="J.A.")
        assert match_patient(hint, theirs).evidence != "remembered"


class TestDeleted:
    def test_a_remembered_patient_now_deleted_is_not_replaced_by_a_weaker_match(
        self, patients, mappings
    ) -> None:
        """ "J.A." meant John Adams; John is gone. Jane Anderson is not J.A. by default."""
        ctx = _ctx(
            patients,
            mappings,
            _patient("john", "John", "Adams"),
            _patient("jane", "Jane", "Anderson"),
        )
        remember_match("simplepractice", "J.A.", "john", ctx)
        patients.delete("john", USER)

        fresh = MatchContext.for_practice(USER, patients, mappings)
        hint = PatientHint(initials="J.A.", source="simplepractice", source_identifier="J.A.")
        result = match_patient(hint, fresh)
        assert result.patient_id is None
        assert result.possible_ids == []
        assert result.evidence is None

    def test_a_deleted_patient_is_never_a_candidate(self, patients, mappings) -> None:
        patients.create(_patient("p1", "Jane", "Adams"), USER)
        patients.create(_patient("p2", "Jane", "Adams"), USER)
        patients.delete("p1", USER)
        ctx = MatchContext.for_practice(USER, patients, mappings)
        result = match_patient(PatientHint(full_name="Jane Adams"), ctx)
        assert (result.patient_id, result.evidence) == ("p2", "full_name")

    def test_a_remembered_deleted_patient_is_not_matched(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        remember_match("simplepractice", "J.A.", "p1", ctx)
        patients.delete("p1", USER)
        fresh = MatchContext.for_practice(USER, patients, mappings)
        hint = PatientHint(initials="J.A.", source="simplepractice", source_identifier="J.A.")
        result = match_patient(hint, fresh)
        assert result.patient_id is None
        assert result.possible_ids == []


class TestNotAClient:
    def test_a_remembered_not_a_client_is_skipped_on_a_later_match(
        self, patients, mappings
    ) -> None:
        """Even a name that uniquely matches a chart does not bring it back."""
        ctx = _ctx(patients, mappings, _patient("p1", "Team", "Meeting"))
        remember_not_a_client("google_calendar", "series:standup", ctx)

        fresh = MatchContext.for_practice(USER, patients, mappings)
        hint = PatientHint(
            full_name="Team Meeting", source="google_calendar", source_identifier="series:standup"
        )
        result = match_patient(hint, fresh)
        assert result.evidence == "not_a_client"
        assert result.patient_id is None
        assert result.possible_ids == []

    def test_not_a_client_replaces_a_client_answer_in_place(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        remember_match("google_calendar", "series:x", "p1", ctx)
        remember_not_a_client("google_calendar", " SERIES:X ", ctx)
        remember_not_a_client("google_calendar", "series:x", ctx)

        [stored] = mappings.list_by_source(USER, "google_calendar")
        assert (stored.source_identifier, stored.answer, stored.patient_id) == (
            "series:x",
            "not_a_client",
            None,
        )

    def test_a_client_answer_can_replace_not_a_client(self, patients, mappings) -> None:
        ctx = _ctx(patients, mappings, _patient("p1", "Jane", "Adams"))
        remember_not_a_client("google_calendar", "series:x", ctx)
        remember_match("google_calendar", "series:x", "p1", ctx)
        hint = PatientHint(source="google_calendar", source_identifier="series:x")
        assert match_patient(hint, ctx).evidence == "remembered"


def test_a_context_over_patients_in_hand_matches_without_a_store() -> None:
    ctx = MatchContext.over([Candidate("p1", "Jane", "Adams", date(1980, 1, 2), None)])
    hint = PatientHint(full_name="Jane Adams", date_of_birth=date(1980, 1, 2))
    assert match_patient(hint, ctx).evidence == "name_and_dob"


def test_a_name_missing_a_middle_name_is_a_question_when_a_chart_has_one() -> None:
    """ "Mary Smith" fits Mary / Smith and Mary Ann / Smith: nobody is guessed."""
    ctx = MatchContext.over(
        [
            Candidate(id="mary", first_name="Mary", last_name="Smith"),
            Candidate(id="mary-ann", first_name="Mary Ann", last_name="Smith"),
        ]
    )

    result = match_patient(PatientHint(full_name="Mary Smith"), ctx)

    assert result.patient_id is None
    assert sorted(result.possible_ids) == ["mary", "mary-ann"]
