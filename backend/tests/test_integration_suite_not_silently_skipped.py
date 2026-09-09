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

    # ``-o addopts=`` clears pyproject's addopts, which carry --cov. Without it
    # each child writes backend/.coverage in the same cwd as the parent, and the
    # --cov-fail-under gate on `make test` becomes a race between whoever writes
    # last.
    #
    # S603: the argv is this interpreter plus literal pytest flags built in this
    # module — no external input reaches it. check=False is the point: the exit
    # code IS the assertion.
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-o", "addopts=", *args],
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


def test_a_supplied_database_url_actually_runs_the_suite() -> None:
    """The bring-your-own-database workflow RUNS, rather than collecting and skipping.

    This is the second door onto the same bug and the sharper test of the two.
    `DATABASE_BACKEND=postgres` used to be set only on the testcontainers path,
    so exporting a real `DATABASE_URL` — the workflow the conftest docstring
    advertises — collected the whole suite and skipped every test of it, exit 0.

    It runs the bootstrap-contract module, which carries no `skipif` and asserts
    what every other module's `skipif` requires. `-p no:randomly` is not needed;
    what matters is that a **skip is not a pass here**: the module either
    executes and asserts, or the assertion never runs and the summary says
    `skipped` instead of `passed`, which the check below rejects.

    No container: a supplied URL takes the early-return path, so this is fast
    and needs no Docker even though it is a real run rather than a collection.
    """
    result = _run_pytest(
        "tests_integration/test_bootstrap_contract.py",
        "-q",
        env_overrides={
            # A real URL as far as the bootstrap is concerned. Nothing connects
            # to it — the contract module only reads environment variables.
            "DATABASE_URL": "postgresql://pablo:pablo_dev@localhost:5432/pablo",
            PLACEHOLDER_MARKER_ENV: None,
        },
    )

    combined = result.stdout + result.stderr
    assert "placeholder DATABASE_URL" not in combined, combined[-2000:]
    assert result.returncode == 0, combined[-2000:]
    # The load-bearing half: "3 passed" and "3 skipped" are both exit 0, and
    # mass-skipping WAS the bug.
    assert "passed" in combined, combined[-2000:]
    assert "skipped" not in combined, (
        "the supplied-database path collected the suite and skipped it — "
        f"PABLO-1vep's other door\n{combined[-2000:]}"
    )


def test_deleting_the_backend_advertisement_is_caught() -> None:
    """A regression in the bootstrap must fail something.

    `DATABASE_BACKEND=postgres` is the single line whose absence turns the whole
    integration suite into skips. Assert that the contract module — not this
    file's own subprocess plumbing — is what notices, by running it with the
    variable forced to a wrong value.
    """
    result = _run_pytest(
        "tests_integration/test_bootstrap_contract.py",
        "-q",
        env_overrides={
            "DATABASE_URL": "postgresql://pablo:pablo_dev@localhost:5432/pablo",
            "DATABASE_BACKEND": "sqlite",  # what a broken bootstrap leaves behind
            PLACEHOLDER_MARKER_ENV: None,
        },
    )

    assert result.returncode != 0, (
        "a bootstrap advertising the wrong backend was not caught; every module "
        f"in the suite would silently skip\n{(result.stdout + result.stderr)[-2000:]}"
    )
    assert "DATABASE_BACKEND=postgres" in result.stdout + result.stderr


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
