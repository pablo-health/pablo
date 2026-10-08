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


@pytest.mark.parametrize("path", _FILES, ids=[p.stem for p in _FILES])
def test_a_templates_diagnoses_are_kept_as_stated(path: Path) -> None:
    """A field that records diagnoses uses the kind that keeps each code apart."""
    spec = PracticeNoteTypeSpec.model_validate(json.loads(path.read_text())["spec"])

    diagnoses = [f for s in spec.sections for f in s.fields if f.key == "diagnoses"]
    assert diagnoses
    assert all(f.kind == "diagnoses" for f in diagnoses)


def _evaluation() -> PracticeNoteTypeSpec:
    return PracticeNoteTypeSpec.model_validate(
        json.loads((TEMPLATES / "psychiatric_evaluation.json").read_text())["spec"]
    )


def test_the_evaluation_offers_both_ways_to_bill_a_first_visit() -> None:
    visit_code = next(i for i in _evaluation().inputs if i.key == "visit_code")

    assert visit_code.kind == "choice"
    assert any("90792" in option for option in visit_code.options)
    assert any("99202-99205" in option for option in visit_code.options)


def test_the_evaluation_keeps_psychotherapy_off_a_diagnostic_evaluation() -> None:
    spec = _evaluation()

    assert spec.sections[-1].key == "psychotherapy"
    assert spec.user_template is not None
    assert "For a psychiatric diagnostic evaluation" in spec.user_template
    assert "leave every field of the Psychotherapy section empty" in spec.user_template


_PRESCRIBER = ("psychiatric_follow_up", "psychiatric_evaluation")


def _spec(name: str) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads((TEMPLATES / f"{name}.json").read_text())["spec"]
    return spec


def _hints(spec: dict[str, Any], section: str) -> dict[str, str]:
    return {
        f["key"]: f["ai_hint"] for s in spec["sections"] if s["key"] == section for f in s["fields"]
    }


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_every_risk_field_asks_for_verbatim_quotation(name: str) -> None:
    """A risk field written as a paraphrase ("the client denied thoughts of...") is a
    claim nobody made; each hint, and the prompt, asks for the words in quotation marks."""
    spec = _spec(name)
    risk = _hints(spec, "risk")
    assert set(risk) >= {
        "suicidal_homicidal_ideation",
        "risk_protective_factors",
        "overall_risk",
        "safety_plan",
    }
    for key, hint in risk.items():
        assert "verbatim, in quotation marks" in hint, key
        assert "paraphrase" in hint, key
    assert "Every risk field holds only words said in the visit" in spec["user_template"]


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_a_catch_all_question_counts_as_asking_about_other_substances(name: str) -> None:
    hint = _hints(_spec(name), "substance_use")["other_substances"]
    assert '"anything else?"' in hint
    assert "counts as asking" in hint
