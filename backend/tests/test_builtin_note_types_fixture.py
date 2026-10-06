# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The frontend's built-in note types fixture is the one the server serves.

Frontend tests render every built-in type from
``frontend/src/test/fixtures/builtinNoteTypes.json``. A section or field added
here without regenerating it would leave those tests rendering a layout no
note has.
"""

from __future__ import annotations

from scripts.regen_builtin_note_types import FIXTURE, render


def test_the_committed_fixture_matches_the_registry() -> None:
    assert FIXTURE.read_text(encoding="utf-8") == render(), (
        "frontend/src/test/fixtures/builtinNoteTypes.json is out of date with the "
        "built-in note types. Regenerate it with "
        "`python backend/scripts/regen_builtin_note_types.py` and commit the result."
    )
