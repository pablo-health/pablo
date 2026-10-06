# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Grade note-type derive against the cases in ``cases.py``.

Each case is a real derive (one proposal call, a rewrite call if anything
was copied, one extraction per sample) plus one extraction of a held-out
note. Hard failures, any of which fails the run:

  - the proposal is missing a part the samples have, or has them out of
    order;
  - any label, hint, description or prompt in the proposal still repeats a
    sample (the same n-gram check the service runs), or names a sentinel —
    a word that appears only in a sample;
  - the held-out note's stray passage, which no field of a note like this
    should take, is not reported unplaced.

Reported, not gated: how many passages of the samples themselves went
unplaced (each one is a field the proposal lacks), how many other
held-out passages did, and how many parts the guard had to rewrite or
neutralize.

Exit code: 0 when there are no hard failures, 1 when there are, 2 on a
setup problem.

    scripts/run-note-type-derive-eval.sh
    scripts/run-note-type-derive-eval.sh --list
    scripts/run-note-type-derive-eval.sh --case dap
    scripts/run-note-type-derive-eval.sh --json
    scripts/run-note-type-derive-eval.sh --model bedrock:<model or inference profile id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import TYPE_CHECKING, Any

from app.services.note_import_service import NoteImportService
from app.services.note_type_derive_checks import SampleText, copied_paths, words
from app.services.note_type_derive_service import NoteTypeDeriveService
from app.services.structured_llm_gateway import (
    StructuredCompletion,
    StructuredLLMGateway,
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

from evals.note_type_derive.cases import DeriveCase, all_cases

if TYPE_CHECKING:
    from app.notes.practice_types import PracticeNoteTypeSpec
    from app.reliability import RetryPolicy
    from app.services.note_type_derive_service import SampleCoverage


def section_order_problem(spec: PracticeNoteTypeSpec, expected: tuple[tuple[str, ...], ...]) -> str:
    """Why the proposal's sections do not cover ``expected`` in order, or ""."""
    texts = [
        words(" ".join([section.label, *(f.label for f in section.fields)]))
        for section in spec.sections
    ]
    position = 0
    for group in expected:
        while position < len(texts) and not any(
            word.startswith(prefix) for prefix in group for word in texts[position]
        ):
            position += 1
        if position == len(texts):
            labels = [s.label for s in spec.sections]
            return f"no section for {'/'.join(group)} in order among {labels}"
        position += 1
    return ""


def sentinels_found(spec: PracticeNoteTypeSpec, sentinels: tuple[str, ...]) -> list[str]:
    text = set(words(spec.model_dump_json()))
    return [s for s in sentinels if s in text]


def grade(
    case: DeriveCase,
    spec: PracticeNoteTypeSpec,
    coverage: list[SampleCoverage],
    held_out: list[SampleCoverage],
    guarded: int,
) -> dict[str, Any]:
    failures: list[str] = []
    order = section_order_problem(spec, case.sections)
    if order:
        failures.append(order)
    copied = copied_paths(spec, SampleText(case.samples)) if case.samples else []
    if copied:
        failures.append(f"sample text in {copied}")
    named = sentinels_found(spec, case.sentinels)
    if named:
        failures.append(f"sentinels in proposal: {named}")
    other_unplaced = 0
    if case.stray is not None:
        (check,) = held_out
        if not check.checked:
            failures.append("held-out note could not be checked")
        elif case.stray not in check.unplaced:
            failures.append("held-out stray passage was placed in a field")
        other_unplaced = len([p for p in check.unplaced if p != case.stray])
    if case.seeded_proposal is not None and guarded == 0:
        failures.append("the proposal was seeded with sample text and the guard found none")
    notes = []
    if case.expect_guard and guarded == 0 and case.seeded_proposal is None:
        notes.append("built to provoke copying, but the model copied nothing to guard")
    return {
        "case": case.name,
        "passed": not failures,
        "failures": failures,
        "notes": notes,
        "sections": [s.label for s in spec.sections],
        "sample_unplaced": sum(len(c.unplaced) for c in coverage),
        "held_out_other_unplaced": other_unplaced,
        "guarded": guarded,
    }


class SeededProposalGateway(StructuredLLMGateway):
    """The model for every call except the proposal, which is ``seeded``."""

    def __init__(self, model: StructuredLLMGateway, seeded: dict[str, Any]) -> None:
        self._model = model
        self._seeded = seeded

    def complete_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float = 0.3,
        thinking_budget: int | None = None,
        timeout_seconds: float | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> StructuredCompletion:
        if response_schema.get("title") == "PracticeNoteTypeSpec":
            return StructuredCompletion(data=self._seeded)
        return self._model.complete_structured(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            thinking_budget=thinking_budget,
            timeout_seconds=timeout_seconds,
            retry_policy=retry_policy,
        )


def _service(model: str | None, seeded: dict[str, Any] | None) -> NoteTypeDeriveService:
    """Real model calls, except a seeded case's proposal."""
    gateway = (
        resolve_structured_llm_gateway(model) if model else get_default_structured_llm_gateway()
    )
    deriving = SeededProposalGateway(gateway, seeded) if seeded else gateway
    return NoteTypeDeriveService(
        NoteImportService(llm_gateway=gateway, model=model), llm_gateway=deriving, model=model
    )


def run_case(model: str | None, case: DeriveCase) -> dict[str, Any]:
    service = _service(model, case.seeded_proposal)
    started = time.monotonic()
    derived = service.derive(list(case.samples), case.description)
    held_out = service.check_coverage(derived.spec, [case.held_out]) if case.held_out else []
    result = grade(case, derived.spec, derived.coverage, held_out, len(derived.guard))
    result["seconds"] = round(time.monotonic() - started, 1)
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cases = [c for c in all_cases() if args.case in c.name]
    if not cases:
        print(f"no case matches {args.case!r}", file=sys.stderr)
        return 2
    if args.list:
        for case in cases:
            kind = "samples" if case.samples else "description"
            print(f"{case.name}  ({kind}, {len(case.sections)} sections)")
        return 0
    on_vertex = not (args.model or "").startswith("bedrock:")
    if on_vertex and not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        print("Set GOOGLE_CLOUD_PROJECT to a project with Vertex access.", file=sys.stderr)
        return 2

    results = [run_case(args.model, case) for case in cases]
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"{mark}  {r['case']}  ({r['seconds']}s)  sections={r['sections']}")
            print(
                f"      sample unplaced={r['sample_unplaced']}  "
                f"held-out other unplaced={r['held_out_other_unplaced']}  "
                f"guarded={r['guarded']}"
            )
            for failure in r["failures"]:
                print(f"      - {failure}")
            for note in r["notes"]:
                print(f"      note: {note}")
        failed = sum(not r["passed"] for r in results)
        print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if any(not r["passed"] for r in results) else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Note-type derive eval")
    p.add_argument("--case", default="", help="substring filter on case name")
    p.add_argument("--json", action="store_true", help="emit raw JSON")
    p.add_argument("--list", action="store_true", help="list the cases and exit")
    p.add_argument("--model", default=None, help="one model id, provider prefix allowed")
    return p.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
