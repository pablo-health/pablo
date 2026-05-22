# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Push the chat eval dataset to Braintrust (THERAPY-exba).

Phase 1.3 deliverable: register the chat dataset as a first-class
Braintrust object. Experiments + scoring land in Phase 1.4
(THERAPY-j39e), once the four custom scorers exist.

Pushes to the `pablo-chat` project so chat experiments stay scoped to
chat — note-generation lives in a separate project for clean baselines.

This test makes real network calls to Braintrust. It is skipped when
`BRAINTRUST_API_KEY` is unset.

    poetry run pytest backend/evals/test_chat.py -v
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

CHAT_PROJECT = "pablo-chat"
CHAT_DATASET = "phase-1-chat"
EXPECTED_CASE_COUNT = 12
EXPECTED_CATEGORIES = {
    "scope_refusal": 4,
    "hallucination_resistance": 5,
    "prompt_injection_resistance": 3,
}


def test_push_chat_dataset() -> None:
    """Load chat.yaml, validate the per-category distribution, push to Braintrust."""
    cases = load_yaml_dataset("chat.yaml")

    assert len(cases) == EXPECTED_CASE_COUNT, (
        f"chat.yaml should have {EXPECTED_CASE_COUNT} cases, got {len(cases)}"
    )

    category_counts: dict[str, int] = {}
    for case in cases:
        assert case.get("surface") == "chat", (
            f"case {case['id']} has surface={case.get('surface')!r}, expected 'chat'"
        )
        category = case.get("category")
        assert category in EXPECTED_CATEGORIES, (
            f"case {case['id']} has unexpected category {category!r}"
        )
        category_counts[category] = category_counts.get(category, 0) + 1

    assert category_counts == EXPECTED_CATEGORIES, (
        f"category distribution {category_counts} != expected {EXPECTED_CATEGORIES}"
    )

    dataset: Any = push_dataset(project=CHAT_PROJECT, name=CHAT_DATASET, cases=cases, sync=True)
    assert dataset is not None
