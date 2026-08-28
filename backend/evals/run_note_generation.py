# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Run the REAL SOAP pipeline over the note-generation dataset and score it.

This closes the loop the LLM-judge spike (``spike_judge.py``) left open: rather
than judging a hand-authored fixture, it drives the actual
``RegistryNoteGenerationService.generate_note`` — the same ``build_soap_prompt``,
structured schema, ``ai_model``, thinking config, and Call-2 source attribution
that production uses — then scores the generated note with the faithfulness
judge against the case's reference SOAP and ``judge_directives``.

That makes it the eval a SOAP-generation change (e.g. capping Call-1 thinking,
decomposing generation) must clear: capture a baseline at today's config, make
the change, re-run, and diff. The hard gate is faithfulness on the highest-stakes
content — invented diagnoses/medications and escalated suicidal ideation (the
``judge_directives`` on the two full-length cases target exactly these).

No BAA is needed: every transcript under ``datasets/`` is synthetic (README rule).
Generation and judging both call Vertex, so this runs ad-hoc / scheduled with
real spend, not in the PR gate.

Usage::

    # Baseline over the full-length (tier-2) cases, write a report:
    GOOGLE_CLOUD_PROJECT=pablohealth-dev \\
    poetry run python -m backend.evals.run_note_generation \\
        --tier 2 --output backend/evals/baselines/note_generation_baseline.json

    # A single case, verbose:
    poetry run python -m backend.evals.run_note_generation --case note-faith-013

    # Repeat each case 3 times and only pass it if every run is clean —
    # a manual check for flaky faithfulness, not something CI runs (it
    # multiplies model spend by the sample count):
    poetry run python -m backend.evals.run_note_generation --tier 2 --samples 3

Exit code is 0 iff every scored case passes the faithfulness gate.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.evals.harness import load_yaml_dataset
from backend.evals.sampling import CaseAggregate, SampleResult, aggregate_sample_verdicts
from backend.evals.scorers.llm_judge_faithfulness import JudgeVerdict, score

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).parent / "datasets"

# Deterministic stand-in identity + date. The transcripts are self-dating in
# their own text; the note's session_date only affects the header line, not the
# faithfulness verdict, so a fixed value keeps runs comparable.
_DEFAULT_SESSION_DATE = datetime(2024, 5, 8, tzinfo=UTC)
_DEFAULT_TRANSCRIPT_FORMAT = "google_meet"  # the dataset transcripts' shape


def _select_cases(
    cases: list[dict[str, Any]], case_ids: list[str] | None, tier: int | None
) -> list[dict[str, Any]]:
    """Pick cases by explicit id list, else by tier, else all note-gen cases."""
    note_cases = [c for c in cases if c.get("surface") == "note_generation"]
    if case_ids:
        wanted = set(case_ids)
        selected = [c for c in note_cases if c.get("id") in wanted]
        missing = wanted - {c.get("id") for c in selected}
        if missing:
            raise SystemExit(f"Cases not found in dataset: {sorted(missing)}")
        return selected
    if tier is not None:
        return [c for c in note_cases if c.get("tier") == tier]
    return note_cases


def _load_reference_soap(path_value: str | None) -> str | None:
    if not path_value:
        return None
    sidecar = DATASETS_DIR / path_value
    if not sidecar.exists():
        raise SystemExit(f"reference_soap_path {path_value!r} -> {sidecar} does not exist")
    return sidecar.read_text(encoding="utf-8")


def _generate_soap(case: dict[str, Any], model: str | None) -> str:
    """Drive the real pipeline for one case; return the SOAP note as JSON text."""
    # Local imports: the generation service pulls in app.services, kept off the
    # module-load path so importing this runner stays cheap.
    from backend.app.models.patient import Patient  # noqa: PLC0415
    from backend.app.models.transcript import Transcript  # noqa: PLC0415
    from backend.app.notes import (  # noqa: PLC0415
        get_default_registry,
        register_builtin_note_types,
    )
    from backend.app.services.note_generation_service import (  # noqa: PLC0415
        RegistryNoteGenerationService,
    )

    # The app registers builtin note types at startup; a bare script must do it
    # itself or the SOAP definition isn't in the registry. Idempotent (replace=True).
    register_builtin_note_types(get_default_registry())

    inputs = case.get("input", {})
    transcript_text = inputs.get("transcript")
    if not transcript_text:
        raise SystemExit(f"Case {case.get('id')!r} has no resolved transcript")

    transcript = Transcript(
        format=inputs.get("transcript_format", _DEFAULT_TRANSCRIPT_FORMAT),
        content=transcript_text,
    )
    # Synthetic patient. diagnosis is intentionally None: intake cases must be
    # documented as a differential, so the pipeline gets no confirmed dx to lean
    # on (a case that supplies one can set input.diagnosis).
    now = _DEFAULT_SESSION_DATE
    patient = Patient(
        id="eval-patient",
        first_name="Eval",
        last_name="Client",
        created_at=now,
        updated_at=now,
        diagnosis=inputs.get("diagnosis"),
    )

    service = RegistryNoteGenerationService(model=model)
    note = service.generate_note("soap", transcript, patient, _DEFAULT_SESSION_DATE)
    return json.dumps(note.content, indent=2)


def _hard_failures(verdict: JudgeVerdict) -> list[str]:
    """The findings that trip the hard gate: HALLUCINATIONS (fabrication).

    A fabricated fact — an invented diagnosis, a med that was never prescribed,
    an escalation of denied suicidal ideation — is the dangerous, ship-blocking
    failure, and it's what the judge detects reliably. Assessment-located
    hallucinations are the worst (risk/diagnosis), so they're labeled as such.

    Omissions are deliberately NOT gated here: on long notes the judge's
    omission *recall* is noisy (it flags safety facts as missing that are
    actually documented in the risk_assessment section), so a high-criticality
    omission is surfaced as *advisory* (see ``_advisory_omissions``) for human
    review, not an automatic fail. The gate protects against the pipeline
    starting to invent things — exactly the regression a thinking-budget change
    could introduce.
    """
    failures: list[str] = []
    for h in verdict.hallucinated_facts:
        where = str(h.get("where", "")).lower()
        label = "ASSESSMENT hallucination" if where == "assessment" else f"hallucination ({where})"
        failures.append(f"{label}: {str(h.get('claim', ''))[:120]}")
    return failures


def _advisory_omissions(verdict: JudgeVerdict) -> list[str]:
    """High-criticality omissions — reported for review, not gated (see above)."""
    return [
        f"high-crit omission: {str(m.get('fact', ''))[:120]}"
        for m in verdict.missing_facts
        if str(m.get("criticality", "")).lower() == "high"
    ]


def _run_sample(
    case: dict[str, Any],
    model: str | None,
    judge_model: str | None,
    transcript_text: str,
    reference_soap: str | None,
    directives: list[str] | None,
) -> SampleResult:
    """Generate + judge the case once and print its status line."""
    print("  generating (real pipeline)...", flush=True)

    start = time.monotonic()
    generated_soap = _generate_soap(case, model)
    gen_elapsed = time.monotonic() - start
    print(f"  generated {len(generated_soap)} chars in {gen_elapsed:.1f}s | judging...", flush=True)

    verdict = score(
        transcript=transcript_text,
        generated_soap=generated_soap,
        reference_soap=reference_soap,
        directives=directives,
        model=judge_model,
    )
    hard = _hard_failures(verdict)
    advisory = _advisory_omissions(verdict)
    sample_passed = not hard  # gate on fabrication; omissions are advisory only

    status = "PASS" if sample_passed else "FAIL"
    print(
        f"  [{status}] hallucinations={len(verdict.hallucinated_facts)} "
        f"(gate) | missing={len(verdict.missing_facts)} advisory-high={len(advisory)}"
    )
    for c in hard:
        print(f"    !! {c}")
    for a in advisory:
        print(f"    ~ {a}")
    if verdict.raw_response == "{}" or (not verdict.judge_notes and not verdict.hallucinated_facts):
        print(f"    judge raw: {verdict.raw_response[:300]!r}")

    return SampleResult(
        hard_failures=hard,
        advisory_omissions=advisory,
        judge_passes=verdict.passes,
        gen_seconds=round(gen_elapsed, 1),
        generated_chars=len(generated_soap),
        hallucinated_facts=verdict.hallucinated_facts,
        missing_facts=verdict.missing_facts,
        judge_notes=verdict.judge_notes,
    )


def _score_case(
    case: dict[str, Any], model: str | None, judge_model: str | None, n_samples: int = 1
) -> dict[str, Any]:
    case_id = case.get("id", "?")
    expected = case.get("expected", {}) or {}
    transcript_text = case.get("input", {}).get("transcript", "")
    reference_soap = _load_reference_soap(expected.get("reference_soap_path"))
    directives = expected.get("judge_directives")

    print(f"\n=== {case_id} ===")
    print(f"  {case.get('description', '')[:200]}")
    print(
        f"  transcript: {len(transcript_text)} chars | reference: {bool(reference_soap)} "
        f"| directives: {len(directives) if directives else 0}"
    )

    samples: list[SampleResult] = []
    for i in range(n_samples):
        if n_samples > 1:
            print(f"  --- sample {i + 1}/{n_samples} ---")
        samples.append(
            _run_sample(case, model, judge_model, transcript_text, reference_soap, directives)
        )

    aggregate: CaseAggregate = aggregate_sample_verdicts(samples)
    first = samples[0]

    if n_samples > 1:
        clean = aggregate.n_samples - aggregate.n_failed_samples
        print(
            f"  case verdict: {'PASS' if aggregate.passed else 'FAIL'} "
            f"({clean}/{aggregate.n_samples} samples clean)"
        )

    return {
        "id": case_id,
        "tier": case.get("tier"),
        "passed": aggregate.passed,
        "judge_passes": first.judge_passes,
        "gen_seconds": first.gen_seconds,
        "generated_chars": first.generated_chars,
        "hallucinated_facts": first.hallucinated_facts,
        "missing_facts": first.missing_facts,
        "hard_failures": aggregate.hard_failures,
        "advisory_omissions": first.advisory_omissions,
        "judge_notes": first.judge_notes,
        "samples": [s.to_dict() for s in samples],
        "n_samples": aggregate.n_samples,
    }


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError(f"--samples must be >= 1, got {n}")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", dest="cases", help="Case id (repeatable)")
    parser.add_argument(
        "--tier", type=int, default=None, help="Filter by tier (e.g. 2 for full-length)"
    )
    parser.add_argument("--dataset", default="note_generation.yaml", help="YAML dataset filename")
    parser.add_argument(
        "--model", default=None, help="SOAP generation model override (default: settings.ai_model)"
    )
    parser.add_argument(
        "--judge-model", default=None, help="Judge model override (default: gemini-3.5-flash)"
    )
    parser.add_argument(
        "--samples",
        type=_positive_int,
        default=1,
        help=(
            "Generate+judge each case N times; a case passes only if every sample is "
            "clean (default: 1). Multiplies model spend by N — a deliberate manual "
            "step, never run in CI."
        ),
    )
    parser.add_argument("--output", default=None, help="Write the full JSON report to this path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    all_cases = load_yaml_dataset(args.dataset)
    selected = _select_cases(all_cases, args.cases, args.tier)
    if not selected:
        raise SystemExit("No matching cases selected")

    print(
        f"Scoring {len(selected)} case(s) via the REAL note pipeline "
        f"(gen model={args.model or 'settings.ai_model'}, judge={args.judge_model or 'default'}, "
        f"samples={args.samples})"
    )

    results = [_score_case(c, args.model, args.judge_model, args.samples) for c in selected]
    n_passed = sum(1 for r in results if r["passed"])
    all_passed = n_passed == len(results)

    report = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "gen_model": args.model or "settings.ai_model",
        "judge_model": args.judge_model or "gemini-3.5-flash",
        "n_cases": len(results),
        "n_passed": n_passed,
        "all_passed": all_passed,
        "samples_per_case": args.samples,
        "cases": results,
    }

    print("\n" + "=" * 60)
    print(f"SUMMARY: {n_passed}/{len(results)} passed")
    for r in results:
        print(
            f"  [{'PASS' if r['passed'] else 'FAIL'}] {r['id']} ({r['gen_seconds']}s, "
            f"{len(r['hard_failures'])} halluc, {len(r['advisory_omissions'])} adv-omit)"
        )
    print("=" * 60)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"Report written to {out_path}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
