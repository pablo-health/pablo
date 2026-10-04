# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which readings of a calendar title are the session's own name."""

from __future__ import annotations

import pytest
from app.patients.titles import TitleReading, title_readings


def _session_names(title: str) -> list[str | None]:
    return [r.full_name for r in title_readings(title) if r.names_the_session and r.full_name]


@pytest.mark.parametrize(
    ("title", "named"),
    [
        ("Jane Smith", ["Jane Smith"]),
        ("Smith, Jane", ["Jane Smith"]),
        ("Jane Smith - Therapy", ["Jane Smith - Therapy", "Jane Smith"]),
        ("Session with Jane Smith", ["Session with Jane Smith", "Jane Smith"]),
        ("Med  Management with Jane Smith", ["Med Management with Jane Smith", "Jane Smith"]),
        ("Lunch with Jane Smith", ["Lunch with Jane Smith"]),
        ("Call with Jane Smith", ["Call with Jane Smith"]),
    ],
)
def test_the_sessions_own_name(title: str, named: list[str]) -> None:
    assert _session_names(title) == named


def test_a_name_only_mentioned_is_still_read_for_suggesting() -> None:
    readings = title_readings("Lunch with Jane Smith")

    assert TitleReading(full_name="Jane Smith") in readings
    [mentioned] = [r for r in readings if r.full_name == "Jane Smith"]
    assert mentioned.names_the_session is False


def test_a_name_found_two_ways_names_the_session_if_either_does() -> None:
    [reading] = [
        r
        for r in title_readings("Jane Smith - Lunch with Jane Smith")
        if r.full_name == "Jane Smith"
    ]

    assert reading.names_the_session is True
