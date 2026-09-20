# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The two copies of the visibility fixture table are the same file.

The table is run by the Python evaluator and by the browser's port of it.
The front end keeps its own copy so it stays buildable on its own, and a
copy is only worth having if something fails when it goes stale. This is
that something: edit
``backend/tests/fixtures/intake_visibility_cases.json``, then run
``python scripts/sync_intake_visibility_fixtures.py``.

Byte-for-byte rather than case-for-case on purpose. A parsed comparison
would pass on two files that differ in a comment, and the comment in that
file is what tells the next person which of the two to edit.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE = REPO_ROOT / "backend" / "tests" / "fixtures" / "intake_visibility_cases.json"
COPY = (
    REPO_ROOT
    / "frontend"
    / "src"
    / "lib"
    / "intake"
    / "__tests__"
    / "fixtures"
    / "intake_visibility_cases.json"
)


def test_the_front_end_copy_is_the_same_file() -> None:
    assert COPY.exists(), f"{COPY} is missing — run scripts/sync_intake_visibility_fixtures.py"
    assert COPY.read_bytes() == SOURCE.read_bytes(), (
        "The front end's copy of the visibility fixtures has gone stale. "
        "Run python scripts/sync_intake_visibility_fixtures.py and commit the result."
    )
