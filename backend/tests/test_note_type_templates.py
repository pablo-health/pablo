# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The note-type templates Settings offers as a starting point.

They ship with the frontend, which sends a template's ``spec`` unchanged when
a practice saves it without edits. So each one must be a definition the save
route accepts, already in the shape the server stores (every default spelled
out), or "start from a template, save" would store something other than the
file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from app.notes.practice_types import SLUG_PATTERN, PracticeNoteTypeSpec

TEMPLATES = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "components"
    / "settings"
    / "noteTypes"
    / "templates"
)

_FILES = sorted(TEMPLATES.glob("*.json"))


def test_there_is_a_template() -> None:
    assert _FILES, f"no templates under {TEMPLATES}"


@pytest.mark.parametrize("path", _FILES, ids=[p.stem for p in _FILES])
def test_template_is_stored_exactly_as_shipped(path: Path) -> None:
    template: dict[str, Any] = json.loads(path.read_text())

    assert re.fullmatch(SLUG_PATTERN.strip("^$"), template["slug"])
    spec = PracticeNoteTypeSpec.model_validate(template["spec"])
    assert spec.model_dump(mode="json") == template["spec"]


@pytest.mark.parametrize("path", _FILES, ids=[p.stem for p in _FILES])
def test_template_has_a_sample_visit(path: Path) -> None:
    samples = json.loads(path.read_text())["samples"]

    assert samples
    assert all(sample["transcript"].strip() for sample in samples)
