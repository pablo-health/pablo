# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Ask the real model what each case's visit changes on the chart, and grade it.

Each case is one proposal call through the same path a draft takes after it
is written (the configured note model, the server-side evidence checks),
graded by the deterministic checks in ``scorers.py``. Any problem from any
check fails the case.

Exit code: 0 when every case passes, 1 when any fails, 2 on a setup problem.

    scripts/run-chart-proposal-eval.sh
    scripts/run-chart-proposal-eval.sh --case unchanged --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any

from app.chart_proposals.drafting import propose_chart_updates
from app.models import Transcript
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

from evals.chart_proposals.cases import ALL_CASES, ProposalCase
from evals.chart_proposals.scorers import grade


def run_case(case: ProposalCase, model: str | None, run: int) -> dict[str, Any]:
    generator = RegistryNoteGenerationService(
        llm_gateway=(
            resolve_structured_llm_gateway(model) if model else get_default_structured_llm_gateway()
        ),
        model=model,
    )
    started = time.monotonic()
    proposals = propose_chart_updates(
        generator.chart_proposal_completion(),
        case.chart,
        Transcript(format="txt", content=case.transcript),
    )
    problems = grade(proposals, case)
    return {
        "case": case.name,
        "run": run,
        "passed": not any(problems.values()),
        "failed_checks": {name: found for name, found in problems.items() if found},
        "seconds": round(time.monotonic() - started, 1),
        "proposals": [asdict(p) for p in proposals],
    }


def _print(results: list[dict[str, Any]]) -> None:
    for r in results:
        print(
            f"{'PASS' if r['passed'] else 'FAIL'}  run {r['run']}  {r['case']}  ({r['seconds']}s)"
        )
        for p in r["proposals"]:
            print(f"      {p['field_key']}: {p['proposed_text']}")
        for name, found in r["failed_checks"].items():
            for problem in found:
                print(f"      {name}: {problem}")
    failed = sum(not r["passed"] for r in results)
    print(f"\n{len(results) - failed}/{len(results)} passed")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chart-proposal eval")
    p.add_argument("--case", default="", help="substring filter on case name")
    p.add_argument("--runs", type=int, default=1, help="run every case this many times")
    p.add_argument("--json", action="store_true", help="emit raw JSON, proposals included")
    p.add_argument("--model", default=None, help="one model id, provider prefix allowed")
    args = p.parse_args(argv)
    cases = [c for c in ALL_CASES if args.case in c.name]
    if not cases:
        print(f"no case matches {args.case!r}", file=sys.stderr)
        return 2
    if not (args.model or "").startswith("bedrock:") and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        print("Set GOOGLE_CLOUD_PROJECT to a project with Vertex access.", file=sys.stderr)
        return 2
    results = [run_case(c, args.model, n) for n in range(1, args.runs + 1) for c in cases]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        _print(results)
    return 1 if any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
