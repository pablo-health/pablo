# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The built-in note types written as spec files.

Each is registered at startup as a built-in and offered in Settings as a base
a practice can adjust. A practice can also detach its adjusted type into a
full copy, so each file must be a definition the save route accepts, already
in the shape the server stores (every default spelled out), or a detached
copy would store something other than the file.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import (
    SLUG_PATTERN,
    PracticeNoteTypeSpec,
    check_against_base,
    resolve,
)
from app.notes.spec_templates import TEMPLATES_DIR

if TYPE_CHECKING:
    from pathlib import Path

TEMPLATES = TEMPLATES_DIR

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
def test_template_is_a_built_in_base(path: Path) -> None:
    template: dict[str, Any] = json.loads(path.read_text())
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)

    base = registry.base_for(template["id"])

    assert base is not None
    assert base.version is None
    assert base.source_spec == PracticeNoteTypeSpec.model_validate(template["spec"])


@pytest.mark.parametrize("path", _FILES, ids=[p.stem for p in _FILES])
def test_template_protects_its_risk_fields_and_psychotherapy_time(path: Path) -> None:
    template: dict[str, Any] = json.loads(path.read_text())
    spec = PracticeNoteTypeSpec.model_validate(template["spec"])
    fields = {f"{s.key}.{f.key}" for s in spec.sections for f in s.fields}
    risk = {p for p in fields if p.startswith("risk.")}

    required = set(template["required_fields"])

    assert required <= fields
    assert risk <= required
    assert "psychotherapy.psychotherapy_time" in required


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
def test_the_clients_words_about_harm_are_quoted_and_never_paraphrased(name: str) -> None:
    """A risk field that paraphrases the client ("the client denied thoughts of...") is
    a claim nobody made; each hint that records what the client said asks for their
    words, quoted, as theirs."""
    spec = _spec(name)
    risk = _hints(spec, "risk")
    quoting = [k for k in ("suicidal_homicidal_ideation", "self_harm_violence") if k in risk]
    assert quoting
    for key in quoting:
        assert "the client's own words quoted verbatim and framed as theirs" in risk[key], key
        assert "Never a paraphrase of the client" in risk[key], key
    assert (
        "own words about suicide, self-harm or violence are quoted verbatim"
        in (spec["user_template"])
    )


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_the_clinicians_findings_are_written_as_findings(name: str) -> None:
    """The note is the clinician's own statement: what the clinician dictated is never
    put in quotation marks or tagged with who said it, and the risk level is only the
    clinician's."""
    spec = _spec(name)
    risk = _hints(spec, "risk")
    for key in ("risk_protective_factors", "overall_risk", "safety_plan"):
        assert "quotation marks" not in risk[key], key
    assert "Never a level the clinician did not say" in risk["overall_risk"]
    assert "written as a finding" in risk["overall_risk"]
    assert "naming who said them" not in json.dumps(spec)
    assert '"Clinician dictated:"' in spec["user_template"]
    assert (
        "In no field write who said, asked, noted or dictated something" in (spec["user_template"])
    )


def test_advice_to_the_client_is_not_recorded_as_self_harm() -> None:
    hint = _hints(_spec("psychiatric_follow_up"), "risk")["self_harm_violence"]
    assert "belong in Emergency instructions" in hint


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_the_place_of_service_reads_in_a_location_and_at_home_when_said(name: str) -> None:
    hint = _hints(_spec(name), "encounter")["place_of_service"]
    assert "located at" not in hint
    assert "The client was in <client location>" in hint
    assert 'does not say so, write "at home in <client location>"' in hint


def test_a_denied_substance_is_marked_as_a_denial() -> None:
    for key, hint in _hints(_spec("psychiatric_follow_up"), "substance_use").items():
        assert "(stated this visit: denied) if the client denied it" in hint, key
        assert '"(asked this visit: no change)" only if the client said nothing' in hint, key


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_a_catch_all_question_counts_as_asking_about_other_substances(name: str) -> None:
    hint = _hints(_spec(name), "substance_use")["other_substances"]
    assert '"anything else?"' in hint
    assert "counts as asking" in hint


_HPI_DOMAINS = (
    "depression",
    "anxiety",
    "insomnia_sleep",
    "inattention_hyperactivity",
    "mania",
    "appetite_eating",
    "onset_duration_course",
    "recent_stressors",
    "functioning",
)


def _fields(name: str, section: str) -> dict[str, dict[str, Any]]:
    return {
        f["key"]: f for s in _spec(name)["sections"] if s["key"] == section for f in s["fields"]
    }


def test_the_follow_up_takes_its_history_by_symptom_domain() -> None:
    subjective = _fields("psychiatric_follow_up", "subjective")

    assert list(subjective) == ["chief_complaint", *_HPI_DOMAINS, "adherence", "side_effects"]
    assert all('"Not discussed."' in subjective[key]["ai_hint"] for key in _HPI_DOMAINS)
    # The history is drafted by a call of its own, which carries the rule; the main
    # draft's prompt has no field it applies to.
    assert "Not discussed" not in _spec("psychiatric_follow_up")["user_template"]
    assert "review-of-systems" not in _spec("psychiatric_evaluation")["user_template"]


def test_the_follow_up_plan_records_education_and_lifestyle_counseling_as_lists() -> None:
    plan = _fields("psychiatric_follow_up", "plan")

    assert plan["education_provided"]["kind"] == "list"
    assert plan["lifestyle_counseling"]["kind"] == "list"
    assert "Leave empty if the clinician explained nothing" in plan["education_provided"]["ai_hint"]
    assert "Leave empty if the clinician gave none" in plan["lifestyle_counseling"]["ai_hint"]


def test_the_medication_plan_asks_why_an_unchanged_dose_continues() -> None:
    hint = _fields("psychiatric_follow_up", "plan")["medication_plan"]["ai_hint"]

    assert "the reason the clinician gave for continuing it" in hint


@pytest.mark.parametrize("name", _PRESCRIBER)
def test_medical_decision_making_is_drafted_for_review_not_for_the_note(name: str) -> None:
    sections = {s["key"]: s for s in _spec(name)["sections"]}

    assert sections["mdm"]["review_only"] is True
    assert list(_fields(name, "mdm")) == ["problems_addressed", "data_reviewed", "management_risk"]
    assert [k for k, s in sections.items() if s.get("review_only")] == ["mdm"]


def test_a_practice_adjustment_of_the_follow_up_still_resolves() -> None:
    """Hide one field and add one: the shape of patch a practice already holds."""
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    base = registry.base_for("psychiatric_follow_up")
    assert base is not None
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Our follow-up",
            "base": "psychiatric_follow_up",
            "patch": {
                "hide_fields": ["subjective.mania"],
                "add_fields": [
                    {
                        "section": "assessment",
                        "field": {"key": "biopsychosocial", "label": "Formulation"},
                        "after": "formulation",
                    }
                ],
                "add_inputs": [{"key": "referral_source", "label": "Referral source"}],
            },
        }
    )

    check_against_base(spec, registry.base_for)
    resolved = resolve("custom.our_follow_up", 1, base, spec)

    sections = {s.key: s for s in resolved.sections}
    assert "mania" not in sections["subjective"].field_keys()
    assert "depression" in sections["subjective"].field_keys()
    assert sections["assessment"].field_keys()[-2:] == ["biopsychosocial", "medical_necessity"]
    assert sections["mdm"].review_only
    assert {"mdm_problems", "new_patient", "referral_source"} <= {i.key for i in resolved.inputs}
