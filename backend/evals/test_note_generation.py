# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Push the note-generation eval dataset to Braintrust (THERAPY-exba).

Phase 1.3 deliverable: register the note-gen dataset as a first-class
Braintrust object. Experiments + scoring land in Phase 1.4
(THERAPY-j39e), once the four custom scorers exist.

Pushes to the `pablo-note-generation` project so note-gen experiments
stay scoped to note-gen — chat lives in a separate project for clean
baselines.

This test makes real network calls to Braintrust. It is skipped when
`BRAINTRUST_API_KEY` is unset.

    poetry run pytest backend/evals/test_note_generation.py -v
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from backend.evals.harness import load_yaml_dataset, push_dataset

pytestmark = pytest.mark.skipif(
    not os.environ.get("BRAINTRUST_API_KEY"),
    reason="BRAINTRUST_API_KEY not set — see backend/evals/README.md",
)

NOTE_GEN_PROJECT = "pablo-note-generation"
NOTE_GEN_DATASET = "phase-1-note-generation"
EXPECTED_CASE_COUNT = 18
EXPECTED_CATEGORIES = {
    "format_adherence": 3,
    "faithfulness": 15,
}


def test_push_note_generation_dataset() -> None:
    """Load note_generation.yaml, validate distribution, push to Braintrust."""
    cases = load_yaml_dataset("note_generation.yaml")

    assert len(cases) == EXPECTED_CASE_COUNT, (
        f"note_generation.yaml should have {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
    )

    category_counts: dict[str, int] = {}
    for case in cases:
        assert case.get("surface") == "note_generation", (
            f"case {case['id']} has surface={case.get('surface')!r}, expected 'note_generation'"
        )
        category = case.get("category")
        assert category in EXPECTED_CATEGORIES, (
            f"case {case['id']} has unexpected category {category!r}"
        )
        category_counts[category] = category_counts.get(category, 0) + 1

    assert category_counts == EXPECTED_CATEGORIES, (
        f"category distribution {category_counts} != expected {EXPECTED_CATEGORIES}"
    )

    dataset: Any = push_dataset(
        project=NOTE_GEN_PROJECT, name=NOTE_GEN_DATASET, cases=cases, sync=True
    )
    assert dataset is not None
