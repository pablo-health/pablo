# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Run the chat eval experiment against the configured model (Phase 1.4 / THERAPY-j39e).

Loads ``datasets/chat.yaml``, pushes the dataset to Braintrust as
``pablo-chat / phase-1-chat`` (idempotent — same name across runs so
experiments compare like-for-like), calls the model through the
Braintrust AI Proxy for each case, and scores with the three Phase 1.4
scorers (``no_confabulation``, ``refusal``, ``instruction_holding``).

Each scorer returns ``{"score": None}`` for cases outside its category,
so the per-scorer aggregate in the Braintrust UI shows only the
relevant subset.

Usage:

    BRAINTRUST_API_KEY=... poetry run python -m backend.evals.run_chat_experiment

Optional env:
    EXPERIMENT_NAME       — defaults to ``phase-1-baseline``
    BRAINTRUST_DEFAULT_MODEL — defaults to ``gemini-2.5-flash``
                              (Vertex publisher path applied by the harness)

The experiment URL is printed to stdout on completion.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from backend.evals.harness import (
    load_yaml_dataset,
    make_model_task,
    push_dataset,
    run_eval,
)
from backend.evals.scorers import (
    instruction_holding_scorer,
    no_confabulation_scorer,
    refusal_scorer,
)

CHAT_PROJECT = "pablo-chat"
CHAT_DATASET = "phase-1-chat"
DEFAULT_EXPERIMENT_NAME = "phase-1-baseline"


def _build_experiment_data(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reshape YAML cases into the ``{input, expected, metadata}`` form
    Braintrust's ``Eval`` data parameter expects.
    """
    out: list[dict[str, Any]] = []
    for case in cases:
        out.append(
            {
                "input": case.get("input", {}),
                "expected": case.get("expected"),
                "metadata": {k: v for k, v in case.items() if k not in {"input", "expected"}},
            }
        )
    return out


def main() -> int:
    cases = load_yaml_dataset("chat.yaml")
    print(f"Loaded {len(cases)} chat cases from chat.yaml")

    # Idempotent push so the dataset object in Braintrust stays
    # in sync with the YAML. Set sync=True so removed YAML cases
    # also get cleaned up from the dataset.
    push_dataset(project=CHAT_PROJECT, name=CHAT_DATASET, cases=cases, sync=True)
    print(f"Pushed dataset '{CHAT_DATASET}' to project '{CHAT_PROJECT}'")

    task = make_model_task()
    experiment_name = os.environ.get("EXPERIMENT_NAME", DEFAULT_EXPERIMENT_NAME)
    model = os.environ.get("BRAINTRUST_DEFAULT_MODEL", "gemini-2.5-flash")

    print(f"Running experiment '{experiment_name}' against model '{model}'")
    run_eval(
        project=CHAT_PROJECT,
        experiment_name=experiment_name,
        dataset=_build_experiment_data(cases),
        task=task,
        scorers=[
            no_confabulation_scorer,
            refusal_scorer,
            instruction_holding_scorer,
        ],
        metadata={"model": model, "dataset": CHAT_DATASET, "phase": "1.4"},
    )

    # The Braintrust SDK auto-prints a "=== SUMMARY ===" block including
    # the experiment URL. We just log a completion marker.
    print("=" * 70)
    print("Experiment complete. See Braintrust URL above for full results.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
