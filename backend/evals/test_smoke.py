# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Smoke test for the eval harness (THERAPY-t0dj).

Validates that the harness can:
  1. Load a YAML dataset
  2. Push it to Braintrust as a named dataset (visible in Datasets tab)
  3. Call a model via the Braintrust AI proxy
  4. Score the output with a trivial scorer
  5. Push the experiment to Braintrust (visible in Experiments tab)

This test makes real network calls to Braintrust and the configured
model provider. It is skipped when `BRAINTRUST_API_KEY` is unset, so
CI / contributors without credentials do not fail.

    poetry run pytest backend/evals/test_smoke.py -v

Real eval suites for chat + note-generation land in THERAPY-exba.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from backend.evals.harness import (
    load_yaml_dataset,
    make_model_task,
    push_dataset,
    require_env,
    run_eval,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("BRAINTRUST_API_KEY"),
    reason="BRAINTRUST_API_KEY not set — see backend/evals/README.md",
)

EXPECTED_CASE_COUNT = 5


def _non_empty_output(*, output: str, **_: Any) -> dict[str, Any]:
    """Trivial scorer: did the model produce non-empty output?

    Real scorers (scope_refusal, faithfulness, prompt_injection,
    format_adherence) land in THERAPY-j39e. This one only proves the
    plumbing.
    """
    return {
        "name": "non_empty_output",
        "score": 1.0 if output and output.strip() else 0.0,
    }


def test_scaffolding_smoke() -> None:
    """End-to-end smoke: load → push dataset → run task → score → push experiment."""
    project = require_env("BRAINTRUST_PROJECT")

    cases = load_yaml_dataset("starter_smoke.yaml")
    assert len(cases) == EXPECTED_CASE_COUNT, (
        f"starter_smoke.yaml should have {EXPECTED_CASE_COUNT} cases"
    )

    dataset = push_dataset(project=project, name="starter-smoke", cases=cases, sync=True)

    task = make_model_task()
    experiment_name = f"scaffolding-smoke-{uuid.uuid4().hex[:8]}"

    result = run_eval(
        project=project,
        experiment_name=experiment_name,
        dataset=dataset,
        task=task,
        scorers=[_non_empty_output],
        metadata={"bead": "THERAPY-t0dj", "purpose": "harness scaffolding"},
    )

    assert result is not None
