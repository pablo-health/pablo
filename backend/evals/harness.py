# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Braintrust eval harness.

Thin wrapper around the Braintrust SDK that pushes a named dataset
(visible in the Braintrust Datasets tab) and runs an experiment
against it. Model calls go through Braintrust's OpenAI-compatible
AI proxy so the harness is provider-agnostic — pick the model by
name (the proxy routes to whichever provider is configured under
the workspace's AI Secrets).

Usage:
    from backend.evals.harness import (
        load_yaml_dataset, push_dataset, make_model_task, run_eval,
    )

    cases = load_yaml_dataset("starter_smoke.yaml")
    dataset = push_dataset(project=PROJECT, name="starter-smoke", cases=cases)
    task = make_model_task()
    run_eval(
        project=PROJECT,
        experiment_name="scaffolding-smoke",
        dataset=dataset,
        task=task,
        scorers=[my_scorer],
    )
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from braintrust import Eval, init_dataset, init_logger
from openai import OpenAI

BRAINTRUST_PROXY_URL = "https://api.braintrust.dev/v1/proxy"
# Matches production. Pablo's chat path resolves to settings.ai_model_flash,
# which is set to gemini-3.5-flash in dev + prod via env-var override.
# Requires the Braintrust workspace's Vertex AI secret to have an empty
# (or "global") location — single-region us-central1 returns 404 on
# multi-region-only models like 3.5-flash. Override with
# BRAINTRUST_DEFAULT_MODEL to compare against other models.
DEFAULT_VERTEX_MODEL = "publishers/google/models/gemini-3.5-flash"
DATASETS_DIR = Path(__file__).parent / "datasets"

EvalCase = dict[str, Any]
TaskFn = Callable[[dict[str, Any]], str]
ScorerFn = Callable[..., dict[str, Any]]


def require_env(var: str) -> str:
    """Read a required env var; fail loudly if missing.

    The harness never prints or logs values from `.env`. Treat keys
    as secrets — rotate them if they leak into chat, screenshots, or
    git history.
    """
    value = os.environ.get(var)
    if not value:
        raise RuntimeError(
            f"{var} not set. Copy backend/evals/.env.example to backend/evals/.env and fill it in."
        )
    return value


def load_yaml_dataset(filename: str) -> list[EvalCase]:
    """Load a YAML dataset from `datasets/`.

    Each case must have an `id` field. Real schema enforcement comes
    with the Phase 1.3 dataset format (THERAPY-exba).
    """
    path = DATASETS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    if not isinstance(cases, list):
        raise ValueError(f"Dataset {filename} must be a YAML list, got {type(cases).__name__}")
    for i, case in enumerate(cases):
        if not isinstance(case, dict) or "id" not in case:
            raise ValueError(f"Case {i} in {filename} missing required `id` field")
    return cases


def push_dataset(
    *,
    project: str,
    name: str,
    cases: list[EvalCase],
    sync: bool = False,
) -> Any:
    """Push (or update) a named dataset in Braintrust.

    Idempotent: each case is upserted by its `id` field (the YAML's
    human-readable id, e.g. `smoke-chat-001`). Re-running the same
    dataset replaces existing records rather than duplicating them.

    With `sync=True`, treat the YAML as the source of truth — any
    record in Braintrust whose id is not in `cases` is deleted. Use
    this for datasets where git is canonical (Phase 1.3 onward).
    Default is `sync=False` so ad-hoc pushes from notebooks don't
    accidentally prune a teammate's records.

    Note: case ids must NEVER be reused. If you delete a case from
    YAML, leave the id gap — past experiments reference records by id,
    and reusing an id silently changes what those experiments scored
    against. Always allocate a fresh id for new cases.
    """
    require_env("BRAINTRUST_API_KEY")
    dataset = init_dataset(project=project, name=name)

    if sync:
        case_ids = {c["id"] for c in cases}
        existing_ids = {record["id"] for record in dataset.fetch()}
        for stale_id in existing_ids - case_ids:
            dataset.delete(stale_id)

    for case in cases:
        dataset.insert(
            id=case["id"],
            input=case.get("input", {}),
            expected=case.get("expected"),
            metadata={k: v for k, v in case.items() if k not in {"input", "expected"}},
        )
    dataset.flush()
    return dataset


def make_model_task(model: str | None = None) -> TaskFn:
    """Return a task function that calls `model` via the Braintrust proxy.

    For chat cases, `input` is expected to have `system`, `context`,
    `user_message`. For note-gen cases, `transcript` + `template`.
    The shape is intentionally flexible at this scaffolding stage —
    THERAPY-exba will lock the schema.
    """
    model_name = model or os.environ.get("BRAINTRUST_DEFAULT_MODEL", DEFAULT_VERTEX_MODEL)
    client = OpenAI(
        api_key=require_env("BRAINTRUST_API_KEY"),
        base_url=BRAINTRUST_PROXY_URL,
    )

    def task(input_data: dict[str, Any]) -> str:
        messages = _build_messages(input_data)
        response = client.chat.completions.create(
            model=model_name,
            messages=messages,  # type: ignore[arg-type]
            temperature=0.0,
            max_tokens=1024,
        )
        return response.choices[0].message.content or ""

    return task


def _build_messages(input_data: dict[str, Any]) -> list[dict[str, str]]:
    """Convert a case `input` dict into OpenAI-style messages."""
    system_parts: list[str] = []
    if sys_prompt := input_data.get("system"):
        system_parts.append(sys_prompt)
    if context := input_data.get("context"):
        system_parts.append(f"Patient context:\n{context}")
    if transcript := input_data.get("transcript"):
        template = input_data.get("template", "SOAP")
        system_parts.append(
            f"Generate a {template} note from the following transcript:\n{transcript}"
        )

    messages: list[dict[str, str]] = []
    if system_parts:
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

    user_msg = input_data.get("user_message")
    if user_msg:
        messages.append({"role": "user", "content": user_msg})
    elif not system_parts:
        messages.append({"role": "user", "content": str(input_data)})
    elif transcript:
        messages.append({"role": "user", "content": "Generate the note now."})

    return messages


def run_eval(
    *,
    project: str,
    experiment_name: str,
    dataset: Any,
    task: TaskFn,
    scorers: list[ScorerFn],
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Run an experiment against `dataset` using `task` and `scorers`.

    `dataset` is normally a Braintrust Dataset object returned by
    `push_dataset()`. Passing a list of cases or a callable also
    works (the Braintrust SDK accepts any of these).
    """
    require_env("BRAINTRUST_API_KEY")
    init_logger(project=project)

    return Eval(
        project,
        experiment_name=experiment_name,
        data=dataset,
        task=task,
        scores=scorers,  # type: ignore[arg-type]
        metadata=metadata or {},
    )
