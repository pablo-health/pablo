# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Parsing and matching the federal exclusion lists. No database, no network.

The LEIE fixture these run against is CAPTURED from the file OIG actually
publishes and then scrubbed, not written here. That distinction is the point:
every structural oddity below — the literal string ``NULL`` in a surname
column, ten zeroes standing in for an absent NPI, eight for an absent date, an
organisation row with three empty name columns — is something the real file
does and an invented fixture would not have thought to do.

Scrubbed means names, NPIs, addresses, cities, ZIPs and birth days are
replaced. The format is untouched: column order, quoting, the placeholders, the
exclusion-type codes, and OIG's own GENERAL/SPECIALTY vocabulary.

Two things are authored rather than captured, and say so where they appear: a
reinstated row (the current file contains none, because OIG removes reinstated
providers) and the SAM.gov payload.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from app.credentialing.exclusions import (
    ExclusionCheckError,
    LeieRecord,
    MatchStrength,
    Outcome,
    Subject,
    check_leie,
    check_sam,
    match_leie,
    normalise_name,
    parse_leie,
)

FIXTURE = Path(__file__).parent / "fixtures" / "leie_sample.csv"

NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def load_fixture() -> list[LeieRecord]:
    return list(parse_leie(FIXTURE.read_text().splitlines()))


class TestParsing:
    def test_reads_every_row_of_the_captured_file(self) -> None:
        assert len(load_fixture()) == 6

    def test_absent_npi_placeholder_becomes_none(self) -> None:
        """Ten zeroes is not an NPI, and matching on it would match everyone."""
        records = load_fixture()
        assert any(record.npi is None for record in records)
        assert all(record.npi != "0000000000" for record in records)

    def test_absent_date_placeholder_becomes_none(self) -> None:
        records = load_fixture()
        assert any(record.date_of_birth is None for record in records)
        assert all(record.reinstated_on is None for record in records)

    def test_organisation_rows_carry_a_business_name_and_no_person(self) -> None:
        records = load_fixture()
        businesses = [record for record in records if record.business_name]
        assert businesses
        assert all(record.last_name is None for record in businesses)

    def test_the_literal_null_surname_survives_as_written(self) -> None:
        """OIG writes the STRING "NULL" where a surname is unknown.

        Captured, not invented. Nothing here translates it to ``None``: it is
        what the file says, and a clinician named NULL is not a case worth
        handling. It is recorded so that a future reader meeting it in
        production knows it is expected.
        """
        assert any(record.last_name == "NULL" for record in load_fixture())

    def test_dates_parse_from_yyyymmdd(self) -> None:
        records = load_fixture()
        excluded = [record.excluded_on for record in records if record.excluded_on]
        assert excluded
        assert all(isinstance(value, date) for value in excluded)

    def test_a_changed_header_raises_rather_than_parsing_nonsense(self) -> None:
        """If OIG moves a column, stop — do not match against the wrong field."""
        with pytest.raises(ExclusionCheckError, match="layout has changed"):
            list(parse_leie(["LASTNAME,FIRSTNAME,SURPRISE", '"A","B","C"']))

    def test_an_empty_file_raises(self) -> None:
        with pytest.raises(ExclusionCheckError, match="empty"):
            list(parse_leie([]))

    def test_a_short_row_is_skipped_not_fatal(self) -> None:
        """One malformed row must not cost the other eighty-four thousand."""
        lines = FIXTURE.read_text().splitlines()
        lines.insert(2, '"TRUNCATED","ROW"')
        assert len(list(parse_leie(lines))) == 6


class TestNormalisation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # An apostrophe closes up and a hyphen opens out, and the real file
            # is why: OIG spells surnames as people do, so both spellings have
            # to survive however she types her own name.
            ("O'Brien", "OBRIEN"),
            ("OBrien", "OBRIEN"),
            ("D'Alise", "DALISE"),
            ("Abad-Santos", "ABAD SANTOS"),
            ("Abad Santos", "ABAD SANTOS"),
            ("Featherstonhaugh, Jr.", "FEATHERSTONHAUGH"),
            ("okonkwo-braithwaite", "OKONKWO BRAITHWAITE"),
            ("  Quispe  ", "QUISPE"),
            ("Vanterpool MD", "VANTERPOOL"),
            (None, ""),
            ("", ""),
        ],
    )
    def test_names_compare_on_letters_alone(self, raw: str | None, expected: str) -> None:
        assert normalise_name(raw) == expected


class TestMatching:
    def test_npi_match_is_the_strongest_answer(self) -> None:
        subject = Subject(last_name="Nobody", npi="1811900055")
        matches = match_leie(load_fixture(), subject)
        assert [match.strength for match in matches] == [MatchStrength.NPI]

    def test_name_and_date_of_birth_together(self) -> None:
        subject = Subject(
            last_name="Featherstonhaugh",
            first_name="Rosalind",
            date_of_birth=date(1963, 6, 12),
        )
        matches = match_leie(load_fixture(), subject)
        assert [match.strength for match in matches] == [MatchStrength.NAME_AND_DOB]

    def test_same_name_different_birthday_is_a_different_person(self) -> None:
        """The single most useful thing a date of birth does here."""
        subject = Subject(
            last_name="Featherstonhaugh",
            first_name="Rosalind",
            date_of_birth=date(1970, 1, 1),
        )
        assert match_leie(load_fixture(), subject) == []

    def test_name_alone_is_a_question_not_a_finding(self) -> None:
        subject = Subject(last_name="Featherstonhaugh", first_name="Rosalind")
        matches = match_leie(load_fixture(), subject)
        assert [match.strength for match in matches] == [MatchStrength.NAME_ONLY]

    def test_a_surname_on_its_own_never_matches(self) -> None:
        """Otherwise every Smith on the list is handed to an operator."""
        assert match_leie(load_fixture(), Subject(last_name="Quispe")) == []

    def test_a_clinician_on_no_list_is_clear(self) -> None:
        subject = Subject(last_name="Ashworth", first_name="Petra", npi="1234567893")
        assert match_leie(load_fixture(), subject) == []

    def test_a_reinstated_row_is_not_reported(self) -> None:
        """AUTHORED edge case: the published file contains no reinstated rows.

        OIG removes a provider on reinstatement rather than dating the row, so
        this shape cannot be captured. It is still guarded, because reporting
        someone the government has reinstated is the worst answer this module
        could give.
        """
        reinstated = LeieRecord(
            last_name="HALLORAN",
            first_name="MARGUERITE",
            middle_name=None,
            business_name=None,
            general="IND- LIC HC SERV PRO",
            specialty="COUNSELOR",
            npi="1811900014",
            date_of_birth=date(1979, 6, 12),
            city="FAIRHAVEN",
            state="OH",
            exclusion_type="1128b4",
            excluded_on=date(2015, 1, 20),
            reinstated_on=date(2021, 3, 1),
        )
        subject = Subject(last_name="Halloran", first_name="Marguerite", npi="1811900014")
        assert match_leie([reinstated], subject) == []

    def test_matches_are_ordered_strongest_first(self) -> None:
        records = load_fixture()
        subject = Subject(
            last_name="Featherstonhaugh",
            first_name="Rosalind",
            npi="1811900063",
        )
        strengths = [match.strength for match in match_leie(records, subject)]
        assert strengths == [MatchStrength.NPI, MatchStrength.NAME_ONLY]


class TestCheckOutcomes:
    def test_nothing_found_is_clear(self) -> None:
        check = check_leie(
            load_fixture(),
            Subject(last_name="Ashworth", first_name="Petra"),
            checked_at=NOW,
            source_as_of=date(2026, 9, 10),
        )
        assert check.outcome is Outcome.CLEAR
        assert check.matches == ()
        assert check.source_as_of == date(2026, 9, 10)

    def test_something_found_is_a_possible_match_not_an_exclusion(self) -> None:
        check = check_leie(
            load_fixture(),
            Subject(last_name="Quispe", first_name="Odalys"),
            checked_at=NOW,
        )
        assert check.outcome is Outcome.POSSIBLE_MATCH
        assert len(check.matches) == 1


class TestSam:
    def test_no_api_key_is_unavailable_and_never_clear(self) -> None:
        """The bug this module exists to fix, one layer down.

        A deployment with no SAM key cannot look. If that rendered as CLEAR,
        every self-hosted install would quietly tell its clinician she had been
        screened against a list nobody asked.
        """
        check = check_sam(Subject(last_name="Quispe"), checked_at=NOW, api_key=None)
        assert check.outcome is Outcome.UNAVAILABLE
        assert check.unavailable_reason
        assert check.matches == ()

    def test_a_service_that_will_not_answer_is_unavailable(self) -> None:
        check = check_sam(
            Subject(last_name="Quispe"),
            checked_at=NOW,
            api_key="test-key",
            base_url="http://127.0.0.1:1/exclusions",
        )
        assert check.outcome is Outcome.UNAVAILABLE
        assert check.unavailable_reason
