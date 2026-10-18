# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Grade the homework read against the synthetic plans in ``cases.py``.

Every proposal still needs the clinician's confirmation, so the question is
how often the read is *confidently wrong* in a way a busy clinician might
click through, and how often it misses what the note plainly asks for.

  - a kept task that is a medication, lab, appointment or crisis step
    (``forbidden``) is a HARD FAILURE: confirmed by mistake, it tells a
    client to change a dose on their own or turns a safety step into a chore;
  - a task on a plan that has none is a HARD FAILURE, for the same reason;
  - a task the call returned but could not quote is dropped before anyone
    sees it, so it costs nothing here; it is counted as ``ungrounded`` and
    reported, because a model that invents often is a model to replace;
  - a task the note asks for that was not found lowers recall. Gated at
    0.85 across the corpus, not per case: a miss means the clinician adds
    it by hand, which is where they were without this feature.

Exit code: 0 when the gates hold, 1 when they do not, 2 on a setup problem.

    scripts/run-homework-proposals-eval.sh
    scripts/run-homework-proposals-eval.sh --case panic
    scripts/run-homework-proposals-eval.sh --json
    scripts/run-homework-proposals-eval.sh --model bedrock:<model or inference profile id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any

from evals.homework_proposals.cases import EvalCase, all_cases

if TYPE_CHECKING:
    from app.homework.drafting import DraftedTask

RECALL_GATE = 0.85


def _read(case: EvalCase, model: str | None) -> tuple[list[DraftedTask], int]:
    """One real read, and how many tasks the call returned before grounding."""
    from app.homework.drafting import HomeworkDrafter, PlanField  # noqa: PLC0415
    from app.services.structured_llm_gateway import (  # noqa: PLC0415
        resolve_structured_llm_gateway,
    )

    fields = [PlanField(f.key, f.label, f.text) for f in case.fields]
    # With --model, that model alone, no fallback behind it; else the app's own routing.
    drafter = (
        HomeworkDrafter(llm_gateway=resolve_structured_llm_gateway(model), model=model)
        if model
        else HomeworkDrafter()
    )
    return drafter.read(fields)


def _grade(case: EvalCase, tasks: list[DraftedTask], returned: int) -> dict[str, Any]:
    texts = [t.text.lower() for t in tasks]
    found = [any(all(w.lower() in t for w in words) for t in texts) for words in case.expected]
    forbidden_hits = sorted({w for w in case.forbidden for t in texts if w.lower() in t})
    matched = {
        i
        for i, t in enumerate(texts)
        for words in case.expected
        if all(w.lower() in t for w in words)
    }
    hard: list[str] = []
    if forbidden_hits:
        hard.append(f"forbidden: {', '.join(forbidden_hits)}")
    if not case.expected and tasks:
        hard.append(f"{len(tasks)} task(s) on a plan with none")
    return {
        "name": case.name,
        "group": case.group,
        "expected": len(case.expected),
        "found": sum(found),
        "kept": len(tasks),
        "extra": len(tasks) - len(matched),
        "ungrounded": max(returned - len(tasks), 0),
        "hard_failures": hard,
        # The model's wording is synthetic here, so it is safe to print.
        "tasks": [t.text for t in tasks],
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sum(r["expected"] for r in results)
    found = sum(r["found"] for r in results)
    recall = found / expected if expected else 1.0
    hard = [r["name"] for r in results if r["hard_failures"]]
    return {
        "cases": len(results),
        "recall": round(recall, 3),
        "recall_gate": RECALL_GATE,
        "hard_failure_cases": hard,
        "extra_tasks": sum(r["extra"] for r in results),
        "ungrounded_tasks": sum(r["ungrounded"] for r in results),
        "overall_pass": not hard and recall >= RECALL_GATE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--case", help="only cases whose name contains this")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--model",
        help="grade this model alone, provider prefix allowed (e.g. bedrock:<model id>)",
    )
    args = parser.parse_args(argv)

    on_bedrock = (args.model or "").startswith("bedrock:")
    if not on_bedrock and not (
        os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
    ):
        print(
            "setup error: GOOGLE_CLOUD_PROJECT must name a project with Vertex access "
            "(gcloud auth application-default login), or pass a bedrock: --model.",
            file=sys.stderr,
        )
        return 2
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

    cases = [c for c in all_cases() if args.case in c.name] if args.case else list(all_cases())
    if not cases:
        print(f"setup error: no case matches {args.case!r}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for case in cases:
        started = time.monotonic()
        try:
            tasks, returned = _read(case, args.model)
        except Exception as exc:  # a model/auth failure is not a verdict on the read
            print(f"read failed on {case.name!r}: {exc}", file=sys.stderr)
            return 2
        result = _grade(case, tasks, returned) | {
            "latency_ms": int((time.monotonic() - started) * 1000)
        }
        results.append(result)
        if not args.json:
            mark = "FAIL" if result["hard_failures"] else "ok  "
            print(
                f"  {mark} {result['latency_ms']:>6}ms {case.group:<6} {case.name:<36} "
                f"found {result['found']}/{result['expected']} kept {result['kept']} "
                f"ungrounded {result['ungrounded']} {'; '.join(result['hard_failures'])}"
            )
            for text in result["tasks"]:
                print(f"         - {text}")

    summary = _summarize(results)
    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        print(json.dumps(summary, indent=2))
    return 0 if summary["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
