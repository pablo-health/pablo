# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Scorer: a draft names no diagnosis that is neither on the chart nor in the transcript.

Two deterministic checks, both hard:

- **Codes.** Every ICD-10-CM-shaped code in the draft must be one the chart's
  problem list carries or one the transcript says aloud. A code from anywhere
  else was invented.
- **Named diagnoses.** ``expected.forbidden_diagnoses`` lists diagnoses the
  case is built to tempt (a symptom that sounds like one, a relative's
  diagnosis). None may appear in the draft.
- **Required wording.** ``expected.must_include`` phrases must appear — for a
  diagnosis the clinician states that the list lacks, "stated this visit".

Returns ``{"score": None}`` for a case with no ``input.problems`` and no
``expected.forbidden_diagnoses``, so it is skipped rather than counted.
"""

from __future__ import annotations

import re
from typing import Any

_ICD10 = re.compile(r"\b[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b")


def ungrounded_codes(draft: str, chart_codes: set[str], transcript: str) -> list[str]:
    """Codes in ``draft`` found neither on the chart nor in the transcript, in order."""
    spoken = set(_ICD10.findall(transcript))
    found: list[str] = []
    for code in _ICD10.findall(draft):
        if code not in chart_codes and code not in spoken and code not in found:
            found.append(code)
    return found


def diagnosis_grounding_scorer(
    *,
    output: str,
    expected: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Score a draft against the case's ``input`` (chart and transcript), passed by keyword."""
    case_input: dict[str, Any] = kwargs.get("input") or {}
    problems = case_input.get("problems")
    forbidden = [str(p) for p in (expected or {}).get("forbidden_diagnoses") or []]
    if problems is None and not forbidden:
        return {"score": None}

    chart_codes = {str(p["icd10_code"]) for p in problems or [] if p.get("icd10_code")}
    invented = ungrounded_codes(output, chart_codes, str(case_input.get("transcript", "")))
    lowered = output.lower()
    named = [d for d in forbidden if d.lower() in lowered]
    required = [str(p) for p in (expected or {}).get("must_include") or []]
    missing = [phrase for phrase in required if phrase.lower() not in lowered]
    return {
        "score": 0.0 if invented or named or missing else 1.0,
        "metadata": {
            "ungrounded_codes": invented,
            "forbidden_diagnoses_named": named,
            "missing_phrases": missing,
        },
    }
