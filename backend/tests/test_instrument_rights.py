# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Every instrument says what may be done with it, and the sold ones stay out.

Two failures this is here to catch, and only one of them is loud.

**An entry with no rights value.** The dataclass makes it a required
argument, so this is really a test that nobody gave the field a default
later. Cheap to keep, and the day it fails it is the only thing standing
between a use-restricted measure and a form that offers it to everybody.

**Wording that should never have been committed.** A ``never_ship``
instrument is a sold product; the registry carries its name and item count
so a practice can recognise it, and carrying its items would be the actual
harm the rights model exists to prevent. That one cannot be caught by
reading the registry, because the wording lives in another module — so this
reads that module's source and asserts the absence.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.intake.items import RESTRICTED_INSTRUMENTS, SELF_REPORT_INSTRUMENTS
from app.outcome_measures import item_text
from app.outcome_measures.instruments import (
    INSTRUMENT_REGISTRY,
    InstrumentValidationError,
    instruments_with_rights,
    validate_item_scores,
)

_RIGHTS_VALUES = {"public_domain", "attestation_required", "never_ship"}


class TestEveryEntryDeclaresItsRights:
    @pytest.mark.parametrize("code", sorted(INSTRUMENT_REGISTRY))
    def test_the_rights_value_is_one_of_the_three(self, code: str) -> None:
        assert INSTRUMENT_REGISTRY[code].rights in _RIGHTS_VALUES

    @pytest.mark.parametrize("code", sorted(INSTRUMENT_REGISTRY))
    def test_there_is_a_line_a_clinician_can_read(self, code: str) -> None:
        """The note is what the settings screen shows beside the checkbox."""
        assert INSTRUMENT_REGISTRY[code].rights_note.strip()

    @pytest.mark.parametrize("code", sorted(INSTRUMENT_REGISTRY))
    def test_there_is_a_name_to_show(self, code: str) -> None:
        assert INSTRUMENT_REGISTRY[code].display_name.strip()

    @pytest.mark.parametrize("code", sorted(INSTRUMENT_REGISTRY))
    def test_a_publisher_url_is_a_url_or_absent(self, code: str) -> None:
        """Absent where there is no stable page; never a half-written one."""
        url = INSTRUMENT_REGISTRY[code].publisher_url
        assert url is None or url.startswith("https://")


class TestNothingSoldIsInTheRepository:
    def test_no_never_ship_instrument_has_item_text(self) -> None:
        for code in instruments_with_rights("never_ship"):
            assert code not in item_text.ITEM_TEXT

    def test_the_item_text_module_does_not_mention_one(self) -> None:
        """Read as source, because the harm is a constant nobody exported.

        ``ITEM_TEXT`` is a mapping somebody has to add a key to; a tuple of
        items sitting in the module under a name of its own is the shape a
        half-finished commit leaves behind, and it would not show up in the
        mapping above.
        """
        source = Path(item_text.__file__).read_text(encoding="utf-8")
        for code in sorted(instruments_with_rights("never_ship")):
            assert code.upper() not in source, f"{code} wording in item_text.py"
            assert f'"{code}"' not in source, f"{code} keyed in item_text.py"

    def test_none_of_them_can_be_put_on_a_form(self) -> None:
        assert instruments_with_rights("never_ship") & SELF_REPORT_INSTRUMENTS == frozenset()

    def test_none_of_them_is_scored_here(self) -> None:
        """No bands, no range: there is nothing to compute without the form."""
        for code in instruments_with_rights("never_ship"):
            assert INSTRUMENT_REGISTRY[code].is_scored is False


class TestTheRestrictedSetIsWhatTheGateReads:
    def test_it_is_the_registry_and_not_a_second_list(self) -> None:
        assert instruments_with_rights("attestation_required") == RESTRICTED_INSTRUMENTS

    def test_it_is_not_empty(self) -> None:
        """A gate with nothing behind it is a gate nobody notices breaking."""
        assert RESTRICTED_INSTRUMENTS


class TestACatalogueEntryIsNotScoreable:
    def test_scoring_one_says_so_rather_than_reporting_a_range(self) -> None:
        """Its item range is empty, so every answer would be 'out of range'."""
        defn = INSTRUMENT_REGISTRY["bdi2"]
        with pytest.raises(InstrumentValidationError, match="not an instrument Pablo scores"):
            validate_item_scores(defn, {"1": 1})

    def test_a_scored_one_is_unaffected(self) -> None:
        validate_item_scores(INSTRUMENT_REGISTRY["phq9"], {"1": 2})
