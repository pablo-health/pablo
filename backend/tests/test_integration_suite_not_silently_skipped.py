# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The integration suite must never report success without running (PABLO-1vep).

``make test-all`` used to run both suites in one pytest invocation. The unit
conftest plants a placeholder ``DATABASE_URL`` so settings validation passes;
the integration conftest read the same variable as "the caller supplied a real
database", stood down without setting ``DATABASE_BACKEND=postgres``, and every
integration module's ``skipif`` fired. Exit code 0, nothing executed.

That is worse than a failure, and it is invisible from inside either suite —
each one passes. So these tests drive **pytest itself** in a subprocess and
assert on its exit code, because the defect only exists in how the two
collections interact. A test that imported the conftest and called
``pytest_configure`` directly would confirm the fix and still miss a regression
in the interaction, which is the only place the bug ever lived.

``--collect-only`` throughout: ``pytest_configure`` runs during startup, before
collection, so the guard fires without anyone needing Docker or a database.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.placeholder_db import PLACEHOLDER_DATABASE_URL, PLACEHOLDER_MARKER_ENV

BACKEND = Path(__file__).resolve().parents[1]


def _run_pytest(
    *args: str, env_overrides: dict[str, str | None]
) -> subprocess.CompletedProcess[str]:
    """Run pytest in a subprocess with a deliberately-constructed environment.

    The parent's ``DATABASE_URL`` and marker are cleared first: this process is
    itself the unit suite, so inheriting its environment wholesale would decide
    the result before the test said anything.
    """
    env = dict(os.environ)
    for key in ("DATABASE_URL", "DATABASE_BACKEND", PLACEHOLDER_MARKER_ENV):
        env.pop(key, None)
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    # S603: the argv is this interpreter plus literal pytest flags built in this
    # module — no external input reaches it. check=False is the point: the exit
    # code IS the assertion.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_placeholder_database_url_makes_the_integration_suite_refuse() -> None:
    """The exact failure mode: placeholder in the environment, integration suite
    asked to run. It must fail loudly rather than collect zero-and-pass."""
    result = _run_pytest(
        "tests_integration/",
        "--collect-only",
        env_overrides={
            "DATABASE_URL": PLACEHOLDER_DATABASE_URL,
            PLACEHOLDER_MARKER_ENV: "1",
        },
    )

    assert result.returncode != 0, (
        "the integration suite reported success while holding the unit suite's "
        f"placeholder DATABASE_URL\nstdout:\n{result.stdout[-2000:]}"
    )
    combined = result.stdout + result.stderr
    assert "placeholder DATABASE_URL" in combined, combined[-2000:]
    # The message has to say what to DO, or it is just a different silent failure.
    assert "separate invocations" in combined, combined[-2000:]


def test_running_both_suites_in_one_invocation_fails_rather_than_skipping() -> None:
    """The combined command that produced the false green.

    No env overrides at all — ``tests/conftest.py`` plants the placeholder on
    its own during startup, exactly as it did when the bug was live.
    """
    result = _run_pytest(
        "tests/",
        "tests_integration/",
        "--collect-only",
        env_overrides={"DATABASE_URL": None},
    )

    assert result.returncode != 0, (
        "`pytest tests/ tests_integration/` reported success; it used to do so "
        f"having skipped every integration module\nstdout:\n{result.stdout[-2000:]}"
    )
    assert "placeholder DATABASE_URL" in result.stdout + result.stderr


def test_a_real_database_url_is_left_alone() -> None:
    """The bring-your-own-database workflow still works.

    The guard keys on the marker, not on the URL text, so a caller who exported
    a real ``DATABASE_URL`` — even one that happens to look like the placeholder
    — is never refused. Collection is expected to succeed here; whether those
    tests would then PASS depends on the database really being there, which is
    not what this asserts.
    """
    result = _run_pytest(
        "tests_integration/",
        "--collect-only",
        env_overrides={
            "DATABASE_URL": PLACEHOLDER_DATABASE_URL,  # same text, no marker
            PLACEHOLDER_MARKER_ENV: None,
        },
    )

    combined = result.stdout + result.stderr
    assert "placeholder DATABASE_URL" not in combined, combined[-2000:]
    assert result.returncode == 0, combined[-2000:]


def test_unit_suite_alone_still_runs() -> None:
    """The placeholder's original job — getting the unit suite past settings
    validation — must survive the fix."""
    result = _run_pytest(
        "tests/test_integration_suite_not_silently_skipped.py",
        "--collect-only",
        env_overrides={"DATABASE_URL": None},
    )

    assert result.returncode == 0, (result.stdout + result.stderr)[-2000:]


@pytest.mark.parametrize("marker_value", ["1", "true", "anything"])
def test_marker_presence_is_what_counts(marker_value: str) -> None:
    """Any non-empty marker means "stand-in", so a future caller setting it to
    something other than ``1`` still gets the guard rather than a silent skip."""
    result = _run_pytest(
        "tests_integration/",
        "--collect-only",
        env_overrides={
            "DATABASE_URL": PLACEHOLDER_DATABASE_URL,
            PLACEHOLDER_MARKER_ENV: marker_value,
        },
    )

    assert result.returncode != 0
    assert "placeholder DATABASE_URL" in result.stdout + result.stderr
