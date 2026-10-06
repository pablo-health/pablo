# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Write the built-in note types, as ``GET /api/note-types`` serves them, to a
fixture the frontend tests render from.

The fixture is produced by the server's own serializer, never written by hand,
so a frontend test of every built-in type sees the layouts the server sends.
``tests/test_builtin_note_types_fixture.py`` fails when it falls behind.

Run from ``backend/``::

    python scripts/regen_builtin_note_types.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.routes.note_types import NoteTypeSchema

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "test"
    / "fixtures"
    / "builtinNoteTypes.json"
)


def builtin_note_types() -> list[dict[str, Any]]:
    """Every built-in type, sorted by key, in the list route's shape."""
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return [NoteTypeSchema.from_def(d).model_dump(mode="json") for d in registry.all()]


def render() -> str:
    return json.dumps(builtin_note_types(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(render())
    print(f"wrote {FIXTURE}")
