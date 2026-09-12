# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Resolving a Tier-0 field to the value sitting behind it.

The confirm tier asks a clinician to agree with a value, so the value has to
reach the card. This is the walk that finds it — the same target resolution
``answered_keys`` does, asking "what is in it" rather than "is anything in it".

Bug classes these cover:
  * the resolver becoming a second read path to the encrypted identifiers.
    SSN, date of birth, tax id and the bank details have exactly one reader —
    ``government_ids.view_identifiers`` — and it writes an audit row naming
    what it decrypted. Nothing here decrypts, so the failure mode is shipping
    ciphertext to a browser rather than plaintext, which is still a disclosure
    of a column that is supposed to have one door.
  * empty rendering as a value. A blank string on a card reads as a confirmed
    fact that happens to look empty, and she would agree to nothing twice.
  * a boolean rendering as ``True`` or ``1`` on a card that asked a yes-or-no
    question.
  * the walk wandering out of Tier 0 into the tiers that ask rather than
    confirm — whose targets sit on the table the encrypted columns live in.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from app.credentialing.checklist import CHECKLIST_FIELDS, TIER_0_CONFIRM, Tier
from app.credentialing.status import (
    _REFUSED_COLUMNS,
    _current_value,
    _is_refused,
    _render,
)

#: Every column in the schema holding an encrypted value, named rather than
#: derived: this list failing to match reality is itself worth knowing about.
ENCRYPTED_COLUMNS = (
    "ssn_encrypted",
    "dob_encrypted",
    "tax_id_encrypted",
    "routing_number_encrypted",
    "account_number_encrypted",
)

_NPI_FIELD = next(f for f in TIER_0_CONFIRM if f.key == "npi_number")


class _Row:
    """Stands in for whatever row a target names."""

    def __init__(self, **columns: object) -> None:
        for name, value in columns.items():
            setattr(self, name, value)


class TestTheEncryptedColumnsAreRefused:
    @pytest.mark.parametrize("column", ENCRYPTED_COLUMNS)
    def test_each_one_by_name(self, column: str) -> None:
        assert _is_refused(column)
        assert column in _REFUSED_COLUMNS

    @pytest.mark.parametrize("column", ENCRYPTED_COLUMNS)
    def test_a_field_pointed_at_one_resolves_to_nothing(self, column: str) -> None:
        # Not an exception: a 500 on the whole checklist would be a worse outcome
        # than an empty card. What matters is that the value does not leave.
        field = replace(_NPI_FIELD, target=f"credential_government_ids.{column}")
        rows = {"credential_government_ids": _Row(**{column: "gAAAAABm-ciphertext"})}

        assert _current_value(field, rows=rows, presented={}) is None

    def test_a_column_added_later_with_the_same_convention_is_refused_too(self) -> None:
        # The named set is the floor, not the ceiling: without this suffix rule
        # a new encrypted column would be readable until someone remembered to
        # come back and list it.
        assert _is_refused("passport_number_encrypted")

    def test_an_ordinary_column_is_not_refused(self) -> None:
        assert not _is_refused("npi_number")
        assert not _is_refused(None)

    def test_no_tier_zero_field_targets_one_today(self) -> None:
        # The refusal above is the backstop. This is the actual state of the
        # question set, and the test that fails first if that changes.
        assert not [f for f in TIER_0_CONFIRM if _is_refused(f.target.partition(".")[2])]


class TestNothingRendersAsAValueUnlessItIsOne:
    def test_an_unset_column_is_nothing(self) -> None:
        assert _render(None) is None

    def test_a_blank_string_is_nothing(self) -> None:
        assert _render("   ") is None

    def test_an_empty_list_is_nothing(self) -> None:
        assert _render([]) is None

    def test_a_list_of_blanks_is_nothing(self) -> None:
        assert _render(["", "  "]) is None

    def test_a_value_is_trimmed_rather_than_shown_with_its_whitespace(self) -> None:
        assert _render("  1999999984 ") == "1999999984"


class TestRenderingForAPersonToRead:
    def test_a_yes_or_no_column_reads_as_yes_or_no(self) -> None:
        # Not "True", and not "1" — bool is a subclass of int and the numeric
        # branch would happily claim it.
        assert _render(True) == "Yes"
        assert _render(False) == "No"

    def test_a_list_column_reads_as_a_sentence(self) -> None:
        assert _render(["LCSW", "LCAS"]) == "LCSW, LCAS"

    def test_a_date_reads_as_a_date(self) -> None:
        assert _render(date(2026, 3, 14)) == "2026-03-14"

    def test_a_timestamp_reads_as_the_day_it_names(self) -> None:
        # The clock is not part of the fact she is confirming.
        assert _render(datetime(2026, 3, 14, 9, 30, tzinfo=UTC)) == "2026-03-14"

    def test_a_number_still_reads(self) -> None:
        assert _render(42) == "42"


class TestResolvingAField:
    def test_a_column_on_her_profile(self) -> None:
        rows = {"clinician_profiles": _Row(npi_number="1999999984")}

        assert _current_value(_NPI_FIELD, rows=rows, presented={}) == "1999999984"

    def test_a_missing_row_is_nothing_on_file(self) -> None:
        assert _current_value(_NPI_FIELD, rows={}, presented={}) is None

    def test_a_field_with_no_home_column_reads_its_confirmation(self) -> None:
        field = next(f for f in TIER_0_CONFIRM if f.key == "legal_name")

        value = _current_value(field, rows={}, presented={"legal_name": "Dana Okafor"})

        assert value == "Dana Okafor"

    def test_the_tier_zero_targets_are_all_reachable(self) -> None:
        """Every card must resolve against a table this walk actually loads.

        A field retargeted onto a table ``current_values`` does not load would
        go on rendering "Nothing on file" forever, which is the bug this is
        fixing — silently, and only for that one card.
        """
        loaded = {"clinician_profiles", "practice_billing_profile", "credential_confirmations"}

        assert {f.target.partition(".")[0] for f in TIER_0_CONFIRM} <= loaded


def test_tier_zero_is_the_only_tier_with_values() -> None:
    """The walk must not wander into the tiers that ask rather than confirm.

    Their targets sit on ``credential_government_ids`` — the table the
    encrypted identifiers live in. Staying out of it entirely is a stronger
    guarantee than refusing its columns one at a time.
    """
    confirm_keys = {f.key for f in TIER_0_CONFIRM}
    other_tables = {
        f.target.partition(".")[0]
        for f in CHECKLIST_FIELDS
        if f.tier is not Tier.CONFIRM and f.key not in confirm_keys
    }

    assert "credential_government_ids" in other_tables
