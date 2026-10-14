# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Draft each starting template's sample visits with the real model, and grade them.

Each case is one generation through the same path a Settings preview takes
(the template's spec as a definition, the entered values validated as the
route validates them), graded by the deterministic checks in
``scorers.py``. Any problem from any check fails the case.

Exit code: 0 when every case passes, 1 when any fails, 2 on a setup problem.

    scripts/run-note-template-eval.sh
    scripts/run-note-template-eval.sh --list
    scripts/run-note-template-eval.sh --case medication-only
    scripts/run-note-template-eval.sh --runs 3
    scripts/run-note-template-eval.sh --json --out /tmp/template-eval
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from datetime import time as clock
from pathlib import Path
from typing import Any

from app.models import Patient, Transcript
from app.notes.practice_types import practice_key, to_definition, validate_note_inputs
from app.services.note_generation_service import GeneratedNote, RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

from evals.note_templates.cases import ALL_CASES, TemplateCase
from evals.note_templates.scorers import (
    grade,
    recorded_boundary,
    therapy_minutes,
    therapy_minutes_table,
)


def draft(case: TemplateCase, model: str | None) -> GeneratedNote:
    definition = to_definition(practice_key(case.template), 0, case.spec)
    inputs = validate_note_inputs(definition, case.inputs)
    now = datetime.now(UTC)
    # A stand-in client; what the chart says comes from the case.
    patient = Patient(id="preview", first_name="", last_name="", created_at=now, updated_at=now)
    # One model alone, with no fallback behind it, so two providers can be
    # compared on the same cases.
    generator = RegistryNoteGenerationService(
        llm_gateway=(
            resolve_structured_llm_gateway(model) if model else get_default_structured_llm_gateway()
        ),
        model=model,
    )
    generated = generator.generate_note(
        definition.key,
        Transcript(format="txt", content=case.transcript),
        patient,
        datetime.combine(case.session_date, clock(15), tzinfo=UTC),
        inputs=inputs,
        definition=definition,
        chart=case.chart,
        client_present_end_seconds=recorded_boundary(case),
    )
    return generated


def run_case(case: TemplateCase, model: str | None, run: int) -> dict[str, Any]:
    started = time.monotonic()
    generated = draft(case, model)
    problems = grade(generated.content, case)
    problems["therapy_minutes"] = therapy_minutes(case, generated.psychotherapy_proposal)
    table = therapy_minutes_table(case, generated.psychotherapy_proposal)
    return {
        "case": case.name,
        "run": run,
        "passed": not any(problems.values()),
        "failed_checks": {name: found for name, found in problems.items() if found},
        "seconds": round(time.monotonic() - started, 1),
        "therapy_seconds": (
            {"proposed": table["proposed"], "labeled": table["labeled"], "end": table["end"]}
            if table
            else None
        ),
        "draft": generated.content,
        "psychotherapy_proposal": generated.psychotherapy_proposal,
    }


def _print(results: list[dict[str, Any]]) -> None:
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        therapy = r["therapy_seconds"]
        minutes = (
            f"  therapy {therapy['proposed'] / 60:.1f} min, labeled {therapy['labeled'] / 60:.1f}"
            if therapy
            else ""
        )
        print(f"{mark}  run {r['run']}  {r['case']}  ({r['seconds']}s){minutes}")
        for name, found in r["failed_checks"].items():
            for problem in found:
                print(f"      {name}: {problem}")
    failed = sum(not r["passed"] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cases = [c for c in ALL_CASES if args.case in c.name]
    if not cases:
        print(f"no case matches {args.case!r}", file=sys.stderr)
        return 2
    if args.list:
        for case in cases:
            print(f"{case.name}  ({case.template} / {case.sample})")
        return 0
    on_vertex = not (args.model or "").startswith("bedrock:")
    if on_vertex and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        print("Set GOOGLE_CLOUD_PROJECT to a project with Vertex access.", file=sys.stderr)
        return 2

    results = [run_case(c, args.model, n) for n in range(1, args.runs + 1) for c in cases]
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(results, indent=2))
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print(results)
    return 1 if any(not r["passed"] for r in results) else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Starting-template eval")
    p.add_argument("--case", default="", help="substring filter on case name")
    p.add_argument("--runs", type=int, default=1, help="draft every case this many times")
    p.add_argument("--json", action="store_true", help="emit raw JSON, drafts included")
    p.add_argument("--out", default=None, help="also write results.json, drafts included, here")
    p.add_argument("--list", action="store_true", help="list the cases and exit")
    p.add_argument("--model", default=None, help="one model id, provider prefix allowed")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
