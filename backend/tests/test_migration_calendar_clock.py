# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The zone a calendar import judges "today" in: the clinician's own, or the
default when the stored one cannot be read."""

from __future__ import annotations

from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from backend.app.migration import jobs


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("America/Chicago", "America/Chicago"),
        ("", jobs.DEFAULT_ZONE),
        ("Not/AZone", jobs.DEFAULT_ZONE),
    ],
)
def test_the_clock_uses_the_clinicians_zone_or_the_default(
    monkeypatch: pytest.MonkeyPatch, stored: str, expected: str
) -> None:
    prefs = SimpleNamespace(timezone=stored)
    repo = SimpleNamespace(get_preferences=lambda _user_id: prefs)
    monkeypatch.setattr(jobs, "get_user_repository", lambda: repo)
    assert jobs._clock("user-1").zone == ZoneInfo(expected)
