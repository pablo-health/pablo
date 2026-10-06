# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Draft each template's sample visit with the real model and grade the draft.

The draft goes through the same generation path a "Try it" preview uses: the
template's spec becomes a definition, and the generator fills it from the
sample transcript and the case's inputs. Hard failures, any of which fails
the run:

  - a field the visit covered came back empty;
  - a field the visit did not cover says something other than "Not stated."
    or "Not asked.";
  - a field that must stay empty (the psychotherapy portion of a visit that
    had none) was filled;
  - a diagnosis lacks a code, carries a code the clinician never said, or a
    stated code is missing; a rule-out was not marked as one;
  - any value contains a forbidden string (a code or level never stated).

Exit code: 0 when there are no hard failures, 1 when there are, 2 on a
setup problem.

    scripts/run-note-type-template-eval.sh
    scripts/run-note-type-template-eval.sh --case psychiatric-evaluation-new-client
    scripts/run-note-type-template-eval.sh --json --out /tmp/template-eval
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models import Patient, Transcript
from app.notes.client_present import client_present_end, segments_from_transcript
from app.notes.diagnoses import diagnosis_text
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.services.note_generation_service import GeneratedNote, RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    StructuredLLMGateway,
    get_default_structured_llm_gateway,
    resolve_structured_llm_gateway,
)

from evals.note_type_templates.cases import (
    NOT_COVERED,
    TemplateCase,
    all_cases,
    load_template,
    sample_transcript,
)


def _is_empty(value: Any) -> bool:
    if isinstance(value, list):
        return not value
    return not str(value or "").strip()


def _says_not_covered(value: Any) -> bool:
    return str(value or "").strip().rstrip(".").lower() in NOT_COVERED


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "; ".join(diagnosis_text(item) for item in value)
    return str(value or "")


def grade(
    case: TemplateCase, content: dict[str, Any], start: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Every hard failure in ``content``, a draft of ``case``'s sample.

    ``start`` is the draft's proposed psychotherapy start.
    """
    failures: list[str] = []
    for section, fields in content.items():
        for key, value in fields.items():
            path = f"{section}.{key}"
            text = _text(value)
            failures.extend(
                f"{path} contains {bad!r}, which was never stated"
                for bad in case.forbidden
                if bad in text
            )
            if path in case.empty:
                if not _is_empty(value):
                    failures.append(f"{path} should be empty")
            elif path in case.not_covered:
                if not _says_not_covered(value):
                    failures.append(f"{path} was not covered but reads {text[:80]!r}")
            elif case.fill_unnamed and path not in case.may_be_empty and _is_empty(value):
                failures.append(f"{path} is empty")
    failures.extend(_grade_quoted(case, content))
    failures.extend(_grade_start(case, start))
    if case.diagnoses_field:
        failures.extend(_grade_diagnoses(case, content))
    return {"case": case.name, "passed": not failures, "failures": failures}


def _grade_start(case: TemplateCase, start: dict[str, Any] | None) -> list[str]:
    """Every proposed therapy start is after the medication portion; the first is at the turn."""
    if case.therapy_starts_between is None:
        return []
    low, high = case.therapy_starts_between
    candidates = [c["seconds"] for c in (start or {}).get("candidates", [])]
    if not candidates:
        return ["no psychotherapy start was proposed"]
    failures = [
        f"proposed therapy start {seconds:.0f}s is before {low:.0f}s"
        for seconds in candidates
        if seconds < low
    ]
    if candidates[0] > high:
        failures.append(f"first proposed therapy start {candidates[0]:.0f}s is after {high:.0f}s")
    return failures


_QUOTE_MARKS = ('"', "\u201c", "\u201d")


def _grade_quoted(case: TemplateCase, content: dict[str, Any]) -> list[str]:
    """Each ``quoted`` field holds its phrase inside quotation marks."""
    failures: list[str] = []
    for path, phrase in case.quoted:
        section, key = path.split(".")
        text = _text(content.get(section, {}).get(key))
        at = text.lower().find(phrase.lower())
        opened = at >= 0 and any(mark in text[:at] for mark in _QUOTE_MARKS)
        closed = at >= 0 and any(mark in text[at + len(phrase) :] for mark in _QUOTE_MARKS)
        if not (opened and closed):
            failures.append(f"{path} does not quote {phrase!r}: {text[:80]!r}")
    return failures


def _grade_diagnoses(case: TemplateCase, content: dict[str, Any]) -> list[str]:
    assert case.diagnoses_field is not None
    section, key = case.diagnoses_field.split(".")
    items = content.get(section, {}).get(key) or []
    failures: list[str] = []
    codes: set[str] = set()
    for item in items:
        code = (item.get("code") or "").strip() if isinstance(item, dict) else ""
        if not code:
            failures.append(f"diagnosis {diagnosis_text(item)!r} has no code")
            continue
        codes.add(code)
        if code not in case.stated_codes:
            failures.append(f"diagnosis code {code!r} was never stated")
        status = str(item.get("status") or "").lower().replace("-", " ")
        if code in case.rule_out and "rule out" not in status:
            failures.append(f"{code} is a rule-out but its status is {status!r}")
    missing = case.stated_codes - codes
    if missing:
        failures.append(f"stated diagnoses missing: {sorted(missing)}")
    return failures


def draft(gateway: StructuredLLMGateway, case: TemplateCase) -> GeneratedNote:
    spec = PracticeNoteTypeSpec.model_validate(load_template(case.template)["spec"])
    definition = to_definition("custom.preview", 0, spec)
    now = datetime.now(UTC)
    # The preview's stand-in client: practice types never read the patient.
    patient = Patient(id="preview", first_name="", last_name="", created_at=now, updated_at=now)
    transcript = Transcript(
        format="txt", content=case.transcript or sample_transcript(case.template, case.sample)
    )
    boundary = (
        client_present_end(segments_from_transcript(transcript), client_channel_expected=True)
        if case.recorded_call
        else None
    )
    generated = RegistryNoteGenerationService(llm_gateway=gateway).generate_note(
        definition.key,
        transcript,
        patient,
        now,
        inputs=case.inputs,
        definition=definition,
        client_present_end_seconds=boundary,
    )
    return generated


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Grade note-type templates on their sample visits")
    p.add_argument("--case", default=None, help="run only this case")
    p.add_argument("--model", default=None, help="model id, e.g. bedrock:<id>")
    p.add_argument("--out", default=None, help="write each draft here as <case>.json")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    args = p.parse_args(argv)

    cases = [c for c in all_cases() if args.case in (None, c.name)]
    if not cases:
        print(f"no case named {args.case!r}", file=sys.stderr)
        return 2
    gateway = (
        resolve_structured_llm_gateway(args.model)
        if args.model
        else get_default_structured_llm_gateway()
    )
    results = []
    for case in cases:
        generated = draft(gateway, case)
        if args.out:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            kept = {**generated.content, "psychotherapy_start": generated.psychotherapy_start}
            (out / f"{case.name}.json").write_text(json.dumps(kept, indent=2) + "\n")
        results.append(grade(case, generated.content, generated.psychotherapy_start))

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for r in results:
            print(f"{'PASS' if r['passed'] else 'FAIL'} {r['case']}")
            for failure in r["failures"]:
                print(f"  - {failure}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
