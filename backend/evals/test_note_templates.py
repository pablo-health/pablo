# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Push the starting-template eval cases to Braintrust.

The cases live in ``note_templates/cases.py`` and draft the templates' own
sample visits; ``note_templates/run.py`` grades real drafts of them. This
pushes the cases (inputs and what each draft may and must say) so runs can
be compared in Braintrust.

Real Braintrust push runs only when BRAINTRUST_API_KEY is set —
otherwise the test is skipped.

    poetry run pytest backend/evals/test_note_templates.py -v
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

import pytest

from backend.evals.note_templates.cases import ALL_CASES, TemplateCase

TEMPLATES_PROJECT = "pablo-note-generation"
TEMPLATES_DATASET = "starting-templates"


def _record(case: TemplateCase) -> dict[str, Any]:
    return {
        "id": f"template-{case.name}",
        "surface": "note_generation",
        "category": "template_adherence",
        "input": {
            "template": case.template,
            "sample": case.sample,
            "session_date": case.session_date.isoformat(),
            "inputs": case.inputs,
            "transcript": case.transcript,
        },
        "expected": asdict(case.expected),
    }


def test_template_cases_shape() -> None:
    """Every case has a unique name and a sample transcript. Runs always."""
    names = [c.name for c in ALL_CASES]
    assert len(names) == len(set(names))
    for case in ALL_CASES:
        assert case.transcript.strip(), case.name


@pytest.mark.skipif(
    not os.environ.get("BRAINTRUST_API_KEY"),
    reason="BRAINTRUST_API_KEY not set — see backend/evals/README.md",
)
def test_push_template_cases() -> None:
    """Push the cases to Braintrust. Real network call; skipped without key."""
    # The harness needs the optional evals group; the shape test above does not.
    harness = pytest.importorskip("backend.evals.harness")
    dataset: Any = harness.push_dataset(
        project=TEMPLATES_PROJECT,
        name=TEMPLATES_DATASET,
        cases=[_record(c) for c in ALL_CASES],
        sync=True,
    )
    assert dataset is not None
