# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Named sets of modules to mutate and the tests that should catch them.

Paths are relative to ``backend/``. A preset's tests are the files that
claim to cover its modules; a mutant only one of them catches still counts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    modules: tuple[str, ...]
    tests: tuple[str, ...]


PRESETS: dict[str, Preset] = {
    # Matching outside calendar events and feeds to clients, and booking them.
    "calendar": Preset(
        modules=(
            "app/patients/matching.py",
            "app/services/outside_sessions.py",
            "app/services/ical_sync_service.py",
        ),
        tests=(
            "tests/test_patient_matching.py",
            "tests/test_practice_wide_matching.py",
            "tests/test_outside_sessions.py",
            "tests/test_outside_sessions_google.py",
            "tests/test_outside_session_routes.py",
            "tests/test_feed_titles.py",
            "tests/test_ical_sync.py",
            "tests/test_calendar_practice_import.py",
        ),
    ),
}
