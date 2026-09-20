# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The form wording and the scorer must describe the same instrument.

``item_text`` and ``instruments`` are two modules that each believe they know
how many items a PHQ-9 has. If they ever disagree, the form asks a question
the scorer will reject, or stops asking one the severity bands were built
around — and the second failure is silent, because a short screener still
sums to a number that looks like a score.
"""

from __future__ import annotations

import pytest
from app.outcome_measures.instruments import INSTRUMENT_REGISTRY
from app.outcome_measures.item_text import (
    FREQUENCY_OPTIONS,
    GAD7_ITEMS,
    ITEM_TEXT,
    PHQ9_ITEMS,
)


@pytest.mark.parametrize(
    ("code", "items"),
    [("phq9", PHQ9_ITEMS), ("gad7", GAD7_ITEMS)],
)
def test_item_count_matches_the_registry(code: str, items: tuple[str, ...]) -> None:
    assert len(items) == INSTRUMENT_REGISTRY[code].item_count


@pytest.mark.parametrize("code", ["phq9", "gad7"])
def test_every_item_key_is_a_key_the_scorer_accepts(code: str) -> None:
    keys = {str(i + 1) for i in range(len(ITEM_TEXT[code]))}
    assert keys == INSTRUMENT_REGISTRY[code].valid_keys


@pytest.mark.parametrize("code", ["phq9", "gad7"])
def test_the_anchors_cover_the_instrument_range(code: str) -> None:
    """Four frequency anchors, one per permitted item value."""
    defn = INSTRUMENT_REGISTRY[code]
    assert [option.value for option in FREQUENCY_OPTIONS] == list(
        range(defn.item_min, defn.item_max + 1)
    )


def test_the_anchors_are_the_published_labels() -> None:
    """Verbatim. A reworded anchor is a different instrument."""
    assert [option.label for option in FREQUENCY_OPTIONS] == [
        "Not at all",
        "Several days",
        "More than half the days",
        "Nearly every day",
    ]


def test_item_text_covers_exactly_the_self_report_screeners() -> None:
    assert set(ITEM_TEXT) == {"phq9", "gad7"}


def test_no_item_is_blank() -> None:
    for items in ITEM_TEXT.values():
        assert all(item.strip() for item in items)
