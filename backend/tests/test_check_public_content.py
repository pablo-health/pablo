# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the public-content checker, focused on binary files.

The checker passes ``--text`` to ``git diff`` on purpose, so that a file git
would otherwise call binary still has its bytes inspected -- the module
docstring records that a single NUL byte once hid a real leak from every text
search. The cost of that flag is that the checker is handed raw binary
whenever a PDF, image or font is committed.

It used to decode that with ``text=True`` and die on the first non-UTF-8 byte,
which is how committing a PDF test fixture took the gate down without anyone
noticing: the job went red for a reason that looked unrelated to its purpose,
and nothing had been inspected.

Forbidden strings are assembled from fragments here for the same reason they
are in the checker itself -- a test that spells one out makes the checker flag
this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_public_content.py"

# Invalid UTF-8: 0x93 is a continuation byte with no lead, which is exactly the
# byte that took the real run down.
_BINARY_BLOB = b"%PDF-1.7\n\x93\x94\x95\xff\xfe binary noise \x00\x93\n%%EOF\n"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    # S603: argv is this interpreter plus a path resolved from __file__ plus
    # flags written in this file. Nothing here comes from outside the test.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(_SCRIPT), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> None:
    # S603/S607: literal git subcommands against a tmp_path repo, no shell.
    # Resolved on PATH deliberately — the test wants whatever git CI has.
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True)  # noqa: S603, S607


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway repo with one commit, so there is a base to diff against."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("hello\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    _git(tmp_path, "branch", "base")
    return tmp_path


def test_binary_file_does_not_crash_the_check(repo: Path) -> None:
    """A committed binary file must be survivable, not fatal."""
    (repo / "fixture.pdf").write_bytes(_BINARY_BLOB)
    _git(repo, "add", "fixture.pdf")
    _git(repo, "commit", "-q", "-m", "add a binary fixture")

    result = _run("--diff-base", "base", cwd=repo)

    assert "UnicodeDecodeError" not in result.stderr
    assert result.returncode == 0, result.stdout + result.stderr


def test_forbidden_string_inside_a_binary_file_is_still_caught(repo: Path) -> None:
    """The point of ``--text``: binary bytes are inspected, not skipped.

    Tolerating undecodable bytes must not become ignoring the file. A forbidden
    ASCII string embedded in an otherwise-binary blob still has to be found,
    which is the property that would silently rot if someone ever "fixed" the
    crash by skipping binary paths instead.
    """
    forbidden = ("Co-Authored-By: " + "Cla" + "ude").encode()
    (repo / "fixture.pdf").write_bytes(_BINARY_BLOB + forbidden + b"\n\x93\xff trailing noise\n")
    _git(repo, "add", "fixture.pdf")
    _git(repo, "commit", "-q", "-m", "add a binary fixture with something in it")

    result = _run("--diff-base", "base", cwd=repo)

    assert result.returncode == 1, result.stdout + result.stderr
    # Failures are reported on stderr; the pass line goes to stdout.
    assert "AI attribution" in result.stdout + result.stderr


def test_clean_text_change_passes(repo: Path) -> None:
    (repo / "notes.md").write_text("an ordinary line of prose\n")
    _git(repo, "add", "notes.md")
    _git(repo, "commit", "-q", "-m", "ordinary change")

    result = _run("--diff-base", "base", cwd=repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_empty_diff_reports_nothing_inspected(repo: Path) -> None:
    """An empty diff is not a pass, and must not read like one."""
    result = _run("--diff-base", "main", cwd=repo)

    assert result.returncode == 0
    assert "NOTHING TO INSPECT" in result.stdout
