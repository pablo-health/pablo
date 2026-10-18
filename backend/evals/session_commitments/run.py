# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Ask the real model what the clinician said they would do in each case, and grade it.

Each case is one commitment call through the path a draft takes after it is
written (the configured note model, then the server-side checks in
``app.chart_proposals.commitments``), graded by ``scorers.py``. Any problem
from any check fails the case. The summary adds up recall over every expected
commitment, the proposals nobody said, client work given to the clinician,
and dates.

Exit code: 0 when every case passes, 1 when any fails, 2 on a setup problem.

    scripts/run-session-commitment-eval.sh --model bedrock:us.anthropic.claude-sonnet-4-6
    scripts/run-session-commitment-eval.sh --case offer --runs 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

from app.chart_proposals.commitments import propose_commitments
from app.models import Transcript
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

from evals.session_commitments.cases import ALL_CASES, VISIT, CommitmentCase
from evals.session_commitments.scorers import grade


def run_case(case: CommitmentCase, model: str | None, run: int) -> dict[str, Any]:
    generator = RegistryNoteGenerationService(
        llm_gateway=(
            resolve_structured_llm_gateway(model) if model else get_default_structured_llm_gateway()
        ),
        model=model,
    )
    started = time.monotonic()
    proposals, error = propose_commitments(
        generator.chart_proposal_completion(),
        Transcript(format="txt", content=case.transcript),
        VISIT,
    )
    graded = grade(proposals or [], case)
    problems = graded.problems
    if error is not None:
        # A call that failed was not graded: say so, rather than as missed commitments.
        problems = {"call_failed": [error]}
    return {
        "case": case.name,
        "run": run,
        "passed": not any(problems.values()),
        "failed_checks": {name: found for name, found in problems.items() if found},
        "seconds": round(time.monotonic() - started, 1),
        "graded": error is None,
        "counts": {
            "expected": graded.expected,
            "found": graded.found,
            "proposed": graded.proposed,
            "invented": graded.invented,
            "client_work": graded.client_work,
            "dated": graded.dated,
            "dated_right": graded.dated_right,
        },
        "proposals": [
            {
                "task": p.proposed_text,
                "evidence": [e.segment_id for e in p.evidence],
                "due_said": p.commitment.due_said if p.commitment else "",
                "due_date": (
                    p.commitment.due_date.isoformat()
                    if p.commitment and p.commitment.due_date
                    else None
                ),
            }
            for p in proposals or []
        ],
    }


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    graded = [r for r in results if r["graded"]]
    keys = graded[0]["counts"] if graded else {}
    total = {key: sum(r["counts"][key] for r in graded) for key in keys}

    def share(part: int, whole: int) -> float | None:
        return round(part / whole, 3) if whole else None

    return {
        "cases_passed": sum(r["passed"] for r in results),
        "cases": len(results),
        "calls_failed": len(results) - len(graded),
        "recall": share(total.get("found", 0), total.get("expected", 0)),
        "precision": share(
            total.get("proposed", 0) - total.get("invented", 0), total.get("proposed", 0)
        ),
        "invented": total.get("invented", 0),
        "client_work_given_to_clinician": total.get("client_work", 0),
        "dates_right": share(total.get("dated_right", 0), total.get("dated", 0)),
    }


def _print(results: list[dict[str, Any]]) -> None:
    for r in results:
        print(
            f"{'PASS' if r['passed'] else 'FAIL'}  run {r['run']}  {r['case']}  ({r['seconds']}s)"
        )
        for p in r["proposals"]:
            due = f"  [{p['due_said']!r} -> {p['due_date']}]" if p["due_said"] else ""
            print(f"      {p['task']}  cites {p['evidence']}{due}")
        for name, found in r["failed_checks"].items():
            for problem in found:
                print(f"      {name}: {problem}")
    s = summary(results)
    print(f"\n{s['cases_passed']}/{s['cases']} cases passed")
    print(
        f"recall {s['recall']}  precision {s['precision']}  invented {s['invented']}  "
        f"client work given to the clinician {s['client_work_given_to_clinician']}  "
        f"dates right {s['dates_right']}  calls failed {s['calls_failed']}"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Session-commitment eval")
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
        print(json.dumps({"results": results, "summary": summary(results)}, indent=2))
    else:
        _print(results)
    return 1 if any(not r["passed"] for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
