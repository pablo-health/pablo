# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Named sets of modules to mutate and the tests that should catch them.

Paths are relative to ``backend/``. A preset's tests are the files that
claim to cover its modules; a mutant only one of them catches still counts.

``integration_tests`` are files under ``tests_integration/``, which need a
Postgres. They run as a second pytest invocation, and only for a mutant the
unit tests let through: the two suites cannot share one (each has its own
``conftest.py`` and the integration one refuses the other's placeholder
database), and a database is the expensive half.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    modules: tuple[str, ...]
    tests: tuple[str, ...]
    integration_tests: tuple[str, ...] = ()


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
    # Making a practice schema's row-level security match the tenant template.
    # The unit tests cover reading the template; what it does to a schema is
    # only visible in a database.
    "rls-apply": Preset(
        modules=("app/db/rls_apply.py",),
        tests=("tests/test_rls_apply_desired.py",),
        integration_tests=("tests_integration/database/test_rls_apply.py",),
    ),
}
