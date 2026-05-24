# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Spike runner for the LLM-judge faithfulness scorer (THERAPY-03m0).

Invokes the judge once against a hand-authored SOAP fixture and prints
the verdict. Used to validate the LLM-judge approach in isolation
from the pipeline integration (per DESIGN.md §7's deferred items).

Usage::

    poetry run python -m backend.evals.spike_judge \\
        --case note-faith-013 \\
        --fixture faith-013_faithful.json

    poetry run python -m backend.evals.spike_judge \\
        --case note-faith-013 \\
        --fixture faith-013_hallucinated.json

The case id resolves to a transcript via the YAML dataset (the case's
``input.transcript_path`` field). The fixture is a JSON file under
``datasets/spike_fixtures/`` containing a SOAPNoteModel-shaped dict
(``subjective``, ``objective``, ``assessment``, ``plan``).

If the case also has a sidecar reference SOAP next to its transcript
(``<name>.soap.txt``), it's passed to the judge for completeness
checks. Otherwise the judge runs in transcript-only mode.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from backend.evals.harness import load_yaml_dataset
from backend.evals.scorers.llm_judge_faithfulness import score

logger = logging.getLogger(__name__)

DATASETS_DIR = Path(__file__).parent / "datasets"
FIXTURES_DIR = DATASETS_DIR / "spike_fixtures"


def _load_case(case_id: str, dataset: str) -> dict:
    cases = load_yaml_dataset(dataset)
    for case in cases:
        if case.get("id") == case_id:
            return case
    raise SystemExit(f"Case {case_id!r} not found in {dataset!r}")


def _load_fixture(fixture_name: str) -> str:
    path = FIXTURES_DIR / fixture_name
    if not path.exists():
        raise SystemExit(f"Fixture not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Strip metadata-only keys (those starting with underscore) and
    # serialize the remainder. The judge gets the four SOAP sections
    # as JSON — same shape SOAPNoteModel would dump.
    soap = {k: v for k, v in data.items() if not k.startswith("_")}
    return json.dumps(soap, indent=2)


def _load_reference_soap(reference_soap_path_value: str | None) -> str | None:
    """Load the reference SOAP referenced by the case's expected block.

    Per DESIGN.md §4, Tier-2 cases carry ``expected.reference_soap_path``
    pointing at a sidecar reference SOAP (e.g.
    ``transcripts/foo.soap.txt``) used by the judge for completeness
    comparison. Returns None for cases that don't carry one.
    """
    if not reference_soap_path_value:
        return None
    sidecar = DATASETS_DIR / reference_soap_path_value
    if not sidecar.exists():
        raise SystemExit(
            f"reference_soap_path {reference_soap_path_value!r} resolves to {sidecar}, "
            "which does not exist"
        )
    with sidecar.open("r", encoding="utf-8") as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Case id (e.g. note-faith-013)")
    parser.add_argument(
        "--fixture", required=True, help="Fixture filename under datasets/spike_fixtures/"
    )
    parser.add_argument(
        "--dataset",
        default="note_generation.yaml",
        help="YAML dataset filename (default: note_generation.yaml)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="LLM model override (default: LLMClient default)",
    )
    parser.add_argument(
        "--no-reference",
        action="store_true",
        help="Skip the reference SOAP sidecar even if present",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    case = _load_case(args.case, args.dataset)
    transcript = case.get("input", {}).get("transcript")
    if not transcript:
        # Case may carry transcript_path only; the harness resolves
        # that to input.transcript at load time, so this branch only
        # fires if the case is misconfigured.
        raise SystemExit(f"Case {args.case!r} has no resolved transcript")

    reference_soap = None
    if not args.no_reference:
        reference_soap_path_value = case.get("expected", {}).get("reference_soap_path")
        reference_soap = _load_reference_soap(reference_soap_path_value)

    generated_soap = _load_fixture(args.fixture)

    print(f"Case: {args.case}")
    print(f"Fixture: {args.fixture}")
    print(f"Reference SOAP loaded: {bool(reference_soap)}")
    print(f"Transcript length: {len(transcript)} chars")
    print(f"Generated SOAP length: {len(generated_soap)} chars")
    print("---")
    print("Calling judge...")
    print()

    verdict = score(
        transcript=transcript,
        generated_soap=generated_soap,
        reference_soap=reference_soap,
        model=args.model,
    )

    print(f"PASSES: {verdict.passes}")
    print()
    print(f"HALLUCINATED FACTS ({len(verdict.hallucinated_facts)}):")
    for h in verdict.hallucinated_facts:
        print(f"  - [{h.get('where', '?')}] {h.get('claim', '')[:120]}")
        if h.get("why_unsupported"):
            print(f"      why: {h['why_unsupported']}")
    print()
    print(f"MISSING FACTS ({len(verdict.missing_facts)}):")
    for m in verdict.missing_facts:
        crit = m.get("criticality", "?")
        print(f"  - [{crit}] {m.get('fact', '')[:140]}")
        if m.get("why_critical"):
            print(f"      why: {m['why_critical']}")
    print()
    print(f"JUDGE NOTES: {verdict.judge_notes}")
    return 0 if verdict.passes else 1


if __name__ == "__main__":
    sys.exit(main())
