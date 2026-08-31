# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Grade the availability-rule parser against the corpus in ``cases.py``.

The grading is deliberately asymmetric, because the two ways of being
wrong are not equally bad:

  - a sentence that must be refused but gets a rule anyway is a HARD
    FAILURE — a confident wrong rule silently blocks or opens a calendar,
    and nothing tells the therapist it happened;
  - a parseable sentence that gets the wrong rules, or only some of them,
    is also a HARD FAILURE — a missing rule in a multi-rule sentence
    silently opens time that was meant to be blocked;
  - the right rules with the wrong enforcement is a soft finding, reported
    but not gated;
  - a parseable sentence the parser refuses is ALWAYS ACCEPTABLE. It falls
    through to the form, which is where the therapist was going anyway.
    Reported as a recall miss, never gated.

Only rule type and params are graded exactly. Enforcement defaults to
"hard" on both sides, so a parser that never reasons about hard-versus-soft
still scores cleanly on rule identity.

Exit code: 0 when there are no hard failures, 1 when there are, 2 on a
setup problem (an unknown filter, or no Vertex project configured).

    scripts/run-availability-parse-eval.sh
    scripts/run-availability-parse-eval.sh --list
    scripts/run-availability-parse-eval.sh --case friday
    scripts/run-availability-parse-eval.sh --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from typing import TYPE_CHECKING, Any

from evals.availability_parse.cases import REFERENCE_DATE, EvalCase, ExpectedRule, all_cases

if TYPE_CHECKING:
    from app.services.availability_parse_service import AvailabilityParseResult


def _canonical_key(rule: ExpectedRule) -> tuple[str, tuple[tuple[str, Any], ...]]:
    """Order-independent identity for a rule, ignoring enforcement.

    Grading compares rule *sets* — "9 to 5 Monday through Thursday" expects
    four rules in any order — so this is the key both sides reduce to.
    """

    def _freeze(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(_freeze(v) for v in value)
        return value

    return (rule.rule_type, tuple(sorted((k, _freeze(v)) for k, v in rule.params.items())))


def _parse_one(phrasing: str) -> AvailabilityParseResult:
    """One real parse. Imported lazily so ``--list`` needs no model access."""
    from app.services.availability_parse_service import (  # noqa: PLC0415
        AvailabilityRuleParseService,
    )

    return AvailabilityRuleParseService().parse(
        phrasing,
        reference_date=date.fromisoformat(REFERENCE_DATE),
    )


def _produced_rules(result: AvailabilityParseResult) -> list[ExpectedRule] | None:
    """The parser's answer in the corpus's own vocabulary, or None to refuse."""
    if result.could_not_parse or not result.proposals:
        return None
    return [
        ExpectedRule(rule_type=p.rule_type, params=p.params, enforcement=p.enforcement)
        for p in result.proposals
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list:
        for case in all_cases():
            kind = "REFUSE  " if case.expected is None else "PARSE   "
            print(f"  {kind}  {case.category:<15} {case.name:<32} {case.phrasing!r}")
        return 0

    if not (os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")):
        print(
            "setup error: GOOGLE_CLOUD_PROJECT must name a project with Vertex "
            "access, and application default credentials must be available "
            "(gcloud auth application-default login).",
            file=sys.stderr,
        )
        return 2
    # Gemini 3.x serves from the global location rather than a single region.
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

    cases = [c for c in all_cases() if args.case in c.name] if args.case else all_cases()
    if not cases:
        print(f"setup error: no case matches {args.case!r}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    for case in cases:
        try:
            result = _parse_one(case.phrasing)
            produced = _produced_rules(result)
        except Exception as exc:  # a model/auth failure is setup, not a verdict
            print(f"\nparse failed on {case.name!r}: {exc}", file=sys.stderr)
            print(
                "Check application default credentials and that "
                "GOOGLE_CLOUD_PROJECT has Vertex access.",
                file=sys.stderr,
            )
            return 2
        results.append(_grade(case, produced, result.exclusive))
        if not args.json:
            _print_case(results[-1])

    summary = _summarize(results)
    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        _print_summary(summary)
    return 0 if summary["overall_pass"] else 1


def _grade(
    case: EvalCase, produced: list[ExpectedRule] | None, produced_exclusive: bool
) -> dict[str, Any]:
    hard: list[str] = []
    soft: list[str] = []
    refused_parseable = False

    if case.expected is None:
        if produced:
            hard.append(
                f"must refuse ({case.category}) but produced {[r.rule_type for r in produced]}"
            )
    elif not produced:
        refused_parseable = True
    else:
        expected_keys = sorted(_canonical_key(r) for r in case.expected)
        produced_keys = sorted(_canonical_key(r) for r in produced)
        if expected_keys != produced_keys:
            hard.append(f"wrong rule set: expected {expected_keys}, got {produced_keys}")
        else:
            expected_enf = sorted((_canonical_key(r), r.enforcement) for r in case.expected)
            produced_enf = sorted((_canonical_key(r), r.enforcement) for r in produced)
            if expected_enf != produced_enf:
                soft.append("rule set correct, enforcement (hard/soft) mismatched")
            if (
                case.expected_exclusive is not None
                and produced_exclusive != case.expected_exclusive
            ):
                soft.append(
                    "rule set correct, exclusive flag mismatched: expected "
                    f"{case.expected_exclusive}, got {produced_exclusive}"
                )

    return {
        "case": case.name,
        "category": case.category,
        "phrasing": case.phrasing,
        "must_refuse": case.expected is None,
        "produced": [r.rule_type for r in produced] if produced else [],
        "refused_parseable_case": refused_parseable,
        "hard_failures": hard,
        "soft_findings": soft,
        "clean": not hard and not soft,
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    positives = [r for r in results if not r["must_refuse"]]
    negatives = [r for r in results if r["must_refuse"]]
    hard_failures = [f"{r['case']}: {f}" for r in results for f in r["hard_failures"]]
    soft_findings = [f"{r['case']}: {f}" for r in results for f in r["soft_findings"]]
    exact = sum(1 for r in positives if r["clean"] and not r["refused_parseable_case"])
    correct_refusals = sum(1 for r in negatives if not r["hard_failures"])

    return {
        "cases": len(results),
        "exact_matches": exact,
        "recall": f"{exact}/{len(positives)}" if positives else "n/a",
        "recall_misses": [r["case"] for r in positives if r["refused_parseable_case"]],
        "correct_refusals": f"{correct_refusals}/{len(negatives)}" if negatives else "n/a",
        "hard_failures": hard_failures,
        "soft_findings": soft_findings,
        "overall_pass": not hard_failures,
    }


def _print_case(r: dict[str, Any]) -> None:
    if r["hard_failures"]:
        status = "FAIL x"
    elif r["soft_findings"]:
        status = "soft ~"
    else:
        status = "PASS  "
    tag = "refuse" if r["refused_parseable_case"] else ",".join(r["produced"]) or "-"
    print(f"  {status}  {r['category']:<15} {r['case']:<32} -> {tag}")
    for f in r["hard_failures"]:
        print(f"          x {f}")
    for f in r["soft_findings"]:
        print(f"          ~ {f}")


def _print_summary(s: dict[str, Any]) -> None:
    bar = "=" * 64
    print(f"\n{bar}")
    print("  availability-parse eval")
    print(f"  recall (exact match on parseable cases) . {s['recall']}")
    if s["recall_misses"]:
        print(f"      refused (acceptable, not gated): {', '.join(s['recall_misses'])}")
    print(f"  correct refusals (must-refuse cases) .... {s['correct_refusals']}")
    print(f"  hard failures ............................ {len(s['hard_failures'])}")
    for f in s["hard_failures"]:
        print(f"      x {f}")
    print(f"  soft findings ............................ {len(s['soft_findings'])}")
    for f in s["soft_findings"]:
        print(f"      ~ {f}")
    print(f"  OVERALL .................................. {'PASS' if s['overall_pass'] else 'FAIL'}")
    print(bar)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Availability-rule parser eval")
    p.add_argument("--case", default="", help="substring filter on case name")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.add_argument("--list", action="store_true", help="list the corpus and exit")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
