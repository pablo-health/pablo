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
    scripts/run-availability-parse-eval.sh --model bedrock:<model or inference profile id>

``--model`` grades one model alone, with no fallback behind it, so two
providers can be compared on the same corpus. A ``bedrock:`` model needs AWS
credentials (``AWS_PROFILE``, or ``AWS_BEDROCK_ROLE_ARN``) with access to it
in ``AWS_BEDROCK_REGION``, and no Vertex project.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date
from typing import TYPE_CHECKING, Any

from app.scheduling_engine.models.appointment_type import AppointmentType

from evals.availability_parse.cases import (
    PRACTICE_APPOINTMENT_TYPES,
    REFERENCE_DATE,
    EvalCase,
    ExpectedRule,
    all_cases,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.services.availability_parse_service import AvailabilityParseResult, ProposedRule

#: The owner every corpus appointment type belongs to. Nothing is stored, so
#: this only has to be a value the parser never reads.
_EVAL_USER_ID = "eval-clinician"


def _canonical_key(rule: ExpectedRule) -> tuple[str, tuple[tuple[str, Any], ...], str | None, bool]:
    """Order-independent identity for a rule, ignoring enforcement.

    Grading compares rule *sets* — "9 to 5 Monday through Thursday" expects
    four rules in any order — so this is the key both sides reduce to. The
    appointment type is part of that identity: a cap on intakes and a cap
    on everything are not the same rule, and grading only rule_type and
    params would call them equal.
    """

    def _freeze(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(_freeze(v) for v in value)
        return value

    return (
        rule.rule_type,
        tuple(sorted((k, _freeze(v)) for k, v in rule.params.items())),
        rule.appointment_type_id,
        rule.allow_other_types,
    )


def _parse_one(phrasing: str, model: str | None = None) -> AvailabilityParseResult:
    """One real parse. Imported lazily so ``--list`` needs no model access.

    With ``model`` (a provider prefix picks the provider), that model alone
    answers; unset, the parser runs as production configures it.
    """
    from app.services.availability_parse_service import (  # noqa: PLC0415
        AvailabilityRuleParseService,
    )
    from app.services.structured_llm_gateway import (  # noqa: PLC0415
        resolve_structured_llm_gateway,
    )

    service = (
        AvailabilityRuleParseService(llm_gateway=resolve_structured_llm_gateway(model), model=model)
        if model
        else AvailabilityRuleParseService()
    )
    return service.parse(
        phrasing,
        reference_date=date.fromisoformat(REFERENCE_DATE),
        appointment_types=_appointment_types(),
    )


def _appointment_types() -> list[AppointmentType]:
    """The corpus's stand-in settings page, as the parser wants it."""
    return [
        AppointmentType(id=type_id, user_id=_EVAL_USER_ID, name=name)
        for type_id, name in PRACTICE_APPOINTMENT_TYPES
    ]


def _produced_rules(result: AvailabilityParseResult) -> list[ExpectedRule] | None:
    """The parser's answer in the corpus's own vocabulary, or None to refuse."""
    if result.could_not_parse or not result.proposals:
        return None
    return _produced_reading(result.proposals)


def _produced_reading(proposals: Sequence[ProposedRule]) -> list[ExpectedRule]:
    """Proposals in the corpus's own vocabulary."""
    return [
        ExpectedRule(
            rule_type=p.rule_type,
            params=p.params,
            enforcement=p.enforcement,
            appointment_type_id=p.appointment_type_id,
            allow_other_types=p.allow_other_types,
        )
        for p in proposals
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list:
        for case in all_cases():
            kind = "REFUSE  " if case.expected is None else "PARSE   "
            print(f"  {kind}  {case.category:<15} {case.name:<32} {case.phrasing!r}")
        return 0

    on_bedrock = (args.model or "").startswith("bedrock:")
    if not on_bedrock and not (
        os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT_ID")
    ):
        print(
            "setup error: GOOGLE_CLOUD_PROJECT must name a project with Vertex "
            "access, and application default credentials must be available "
            "(gcloud auth application-default login).",
            file=sys.stderr,
        )
        return 2
    # Gemini 3.x serves from the global location rather than a single region.
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
    setup_hint = (
        "Check that AWS credentials are available (AWS_PROFILE or "
        "AWS_BEDROCK_ROLE_ARN) and have access to the model in AWS_BEDROCK_REGION."
        if on_bedrock
        else "Check application default credentials and that "
        "GOOGLE_CLOUD_PROJECT has Vertex access."
    )

    cases = [c for c in all_cases() if args.case in c.name] if args.case else all_cases()
    if not cases:
        print(f"setup error: no case matches {args.case!r}", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    failed_calls: list[str] = []
    latencies: list[int] = []
    for case in cases:
        started = time.monotonic()
        try:
            result = _parse_one(case.phrasing, args.model)
        except Exception as exc:  # a model/auth failure is not a verdict on the parser
            if not latencies:
                print(f"\nparse failed on {case.name!r}: {exc}", file=sys.stderr)
                print(setup_hint, file=sys.stderr)
                return 2
            # Once calls have succeeded, one that still fails after its retry
            # is what a therapist would have seen as an error: counted with
            # the latencies, never graded as a parse.
            latencies.append(_elapsed_ms(started))
            failed_calls.append(case.name)
            if not args.json:
                print(
                    f"  ERROR   {latencies[-1]:>6}ms  {case.category:<15} "
                    f"{case.name:<32} -> {type(exc).__name__}"
                )
            continue
        latencies.append(_elapsed_ms(started))
        results.append(
            _grade(case, _produced_rules(result), result.exclusive, result)
            | {"latency_ms": latencies[-1]}
        )
        if not args.json:
            _print_case(results[-1])

    summary = _summarize(results) | {
        "failed_calls": failed_calls,
        "latency_ms": _latency_summary(latencies),
    }
    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2))
    else:
        _print_summary(summary)
    return 0 if summary["overall_pass"] else 1


def _grade(
    case: EvalCase,
    produced: list[ExpectedRule] | None,
    produced_exclusive: bool,
    result: AvailabilityParseResult | None = None,
) -> dict[str, Any]:
    hard: list[str] = []
    soft: list[str] = []
    refused_parseable = False
    offered_readings = len(result.readings) if result is not None else 0
    # Whether the parser named the expected missing type, as a yes/no. The
    # model's own wording stays out of the report: it is free text, and the
    # report is printed.
    named_expected_type: bool | None = None
    if case.expected_unknown_type is not None:
        named = (result.unknown_appointment_type if result is not None else None) or ""
        want = case.expected_unknown_type.casefold().split()[0]
        named_expected_type = want in named.casefold()

    if case.expected is None:
        if produced:
            hard.append(
                f"must refuse ({case.category}) but produced {[r.rule_type for r in produced]}"
            )
        else:
            # Helpfulness of a correct refusal: soft, never gated. A refusal
            # that asks without offering the choice, or refuses a missing
            # type without naming it, is still safe.
            if case.expects_two_readings and offered_readings != 2:
                soft.append(f"refused, but offered {offered_readings} readings, not 2")
            hard.extend(_readings_mismatch(case, result))
            if named_expected_type is False:
                soft.append(
                    f"refused, but did not name the missing type {case.expected_unknown_type!r}"
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
        "offered_readings": offered_readings,
        "named_expected_type": named_expected_type,
        "hard_failures": hard,
        "soft_findings": soft,
        "clean": not hard and not soft,
    }


def _readings_mismatch(case: EvalCase, result: AvailabilityParseResult | None) -> list[str]:
    """Why the offered readings are wrong; empty if they are right or absent.

    Readings that are offered are a choice the therapist acts on, so a wrong
    one is graded like a wrong rule set. Readings that are missing are only
    less helpful, and graded soft elsewhere.
    """
    if case.expected_readings is None or result is None or not result.readings:
        return []
    expected = sorted(sorted(_canonical_key(r) for r in rs) for rs in case.expected_readings)
    offered = sorted(
        sorted(_canonical_key(r) for r in _produced_reading(reading.proposals))
        for reading in result.readings
    )
    if offered == expected:
        return []
    return [f"wrong readings: expected {expected}, got {offered}"]


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


def _elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def _latency_summary(latencies: list[int]) -> dict[str, int] | None:
    """p50, p95 and max of the per-case parse time, failed calls included.

    Nearest-rank percentiles: the corpus is under a hundred cases, so an
    interpolated p95 would report a time no case actually took.
    """
    if not latencies:
        return None
    ordered = sorted(latencies)

    def rank(p: float) -> int:
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)]

    return {"p50": rank(0.50), "p95": rank(0.95), "max": ordered[-1]}


def _print_case(r: dict[str, Any]) -> None:
    if r["hard_failures"]:
        status = "FAIL x"
    elif r["soft_findings"]:
        status = "soft ~"
    else:
        status = "PASS  "
    tag = "refuse" if r["refused_parseable_case"] else ",".join(r["produced"]) or "-"
    print(f"  {status}  {r['latency_ms']:>6}ms  {r['category']:<15} {r['case']:<32} -> {tag}")
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
    if s["failed_calls"]:
        print(f"  failed calls (not graded) ................ {len(s['failed_calls'])}")
        print(f"      {', '.join(s['failed_calls'])}")
    if s["latency_ms"]:
        lat = s["latency_ms"]
        print(
            f"  latency (per parse) ...................... "
            f"p50 {lat['p50']}ms  p95 {lat['p95']}ms  max {lat['max']}ms"
        )
    print(f"  OVERALL .................................. {'PASS' if s['overall_pass'] else 'FAIL'}")
    print(bar)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Availability-rule parser eval")
    p.add_argument("--case", default="", help="substring filter on case name")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.add_argument("--list", action="store_true", help="list the corpus and exit")
    p.add_argument(
        "--model",
        default=None,
        help="grade this model alone, provider prefix allowed "
        "(e.g. bedrock:<model id>); default is the configured parser",
    )
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
