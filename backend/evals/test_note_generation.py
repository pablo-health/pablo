# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Push the note-generation eval dataset to Braintrust.

Per DESIGN.md §4, this test asserts *structural* invariants on the
dataset and pushes to Braintrust. There is no `EXPECTED_CASE_COUNT`
constant — counting cases is bookkeeping, not testing.

Invariants asserted here:
  - Every case has a recognized `surface` and `category`.
  - Every case carries a `tier` field of 1 or 2.
  - Tier-1 cases have *no* Tier-2-only fields (reference_soap_path,
    judge_directives), and vice versa.
  - Tier-2 cases carry either a `reference_soap_path` or
    `judge_directives` (otherwise the judge has no input to work with).

Real Braintrust push runs only when BRAINTRUST_API_KEY is set —
otherwise the test is skipped.

    poetry run pytest backend/evals/test_note_generation.py -v
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from backend.evals.harness import load_yaml_dataset, push_dataset

VALID_CATEGORIES = {"format_adherence", "faithfulness"}
VALID_TIERS = {1, 2}
TIER_2_ONLY_FIELDS = {"reference_soap_path", "judge_directives"}

NOTE_GEN_PROJECT = "pablo-note-generation"
NOTE_GEN_DATASET = "phase-1-note-generation"


def _assert_case_shape(case: dict[str, Any]) -> None:
    """Per-case structural checks (DESIGN.md §4)."""
    cid = case.get("id", "<unknown>")
    assert case.get("surface") == "note_generation", (
        f"case {cid} has surface={case.get('surface')!r}, expected 'note_generation'"
    )
    assert case.get("category") in VALID_CATEGORIES, (
        f"case {cid} has unexpected category {case.get('category')!r}"
    )
    tier = case.get("tier")
    assert tier in VALID_TIERS, f"case {cid} has invalid or missing tier={tier!r}"

    expected = case.get("expected") or {}
    tier_2_fields_present = TIER_2_ONLY_FIELDS & set(expected.keys())
    if tier == 1:
        assert not tier_2_fields_present, (
            f"case {cid} is tier 1 but has tier-2-only fields: {tier_2_fields_present}"
        )
    elif tier == 2:
        assert tier_2_fields_present, (
            f"case {cid} is tier 2 but has none of {TIER_2_ONLY_FIELDS} "
            f"in expected; the judge has nothing to work with"
        )


def test_note_generation_dataset_shape() -> None:
    """Structural invariants on the YAML. Runs always (no Braintrust call)."""
    cases = load_yaml_dataset("note_generation.yaml")
    assert cases, "note_generation.yaml is empty"
    for case in cases:
        _assert_case_shape(case)


@pytest.mark.skipif(
    not os.environ.get("BRAINTRUST_API_KEY"),
    reason="BRAINTRUST_API_KEY not set — see backend/evals/README.md",
)
def test_push_note_generation_dataset() -> None:
    """Push the dataset to Braintrust. Real network call; skipped without key."""
    cases = load_yaml_dataset("note_generation.yaml")
    for case in cases:
        _assert_case_shape(case)
    dataset: Any = push_dataset(
        project=NOTE_GEN_PROJECT, name=NOTE_GEN_DATASET, cases=cases, sync=True
    )
    assert dataset is not None
