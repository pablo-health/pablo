# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Source attribution service for linking SOAP claims to transcript segments."""

import json

from ..models import (
    SOAPNote,
    SOAPSentence,
)

# Key format: "section.field" or "section.field.index" for list items
ClaimKey = str


def build_claims_from_soap(soap_note: SOAPNote) -> dict[ClaimKey, SOAPSentence]:
    """Extract all SOAPSentence objects from a SOAPNote with dotted-path keys."""
    claims: dict[ClaimKey, SOAPSentence] = {}

    # Subjective
    s = soap_note.subjective
    _add_field(claims, "subjective.chief_complaint", s.chief_complaint)
    _add_field(claims, "subjective.mood_affect", s.mood_affect)
    _add_list(claims, "subjective.symptoms", s.symptoms)
    _add_field(claims, "subjective.client_narrative", s.client_narrative)

    # Objective
    o = soap_note.objective
    _add_field(claims, "objective.appearance", o.appearance)
    _add_field(claims, "objective.behavior", o.behavior)
    _add_field(claims, "objective.speech", o.speech)
    _add_field(claims, "objective.thought_process", o.thought_process)
    _add_field(claims, "objective.affect_observed", o.affect_observed)

    # Assessment
    a = soap_note.assessment
    _add_field(claims, "assessment.clinical_impression", a.clinical_impression)
    _add_field(claims, "assessment.progress", a.progress)
    _add_field(claims, "assessment.risk_assessment", a.risk_assessment)
    _add_field(claims, "assessment.functioning_level", a.functioning_level)

    # Plan
    p = soap_note.plan
    _add_list(claims, "plan.interventions_used", p.interventions_used)
    _add_list(claims, "plan.homework_assignments", p.homework_assignments)
    _add_list(claims, "plan.next_steps", p.next_steps)
    _add_field(claims, "plan.next_session", p.next_session)

    return claims


def _add_field(claims: dict[ClaimKey, SOAPSentence], key: str, sentence: SOAPSentence) -> None:
    if sentence.text.strip():
        claims[key] = sentence


def _add_list(
    claims: dict[ClaimKey, SOAPSentence],
    prefix: str,
    items: list[SOAPSentence] | None,
) -> None:
    if not items:
        return
    for i, sentence in enumerate(items):
        if sentence.text.strip():
            claims[f"{prefix}.{i}"] = sentence


def format_transcript_with_segment_ids(transcript_content: str) -> str:
    """Prepend [Sn] segment indices to each transcript line.

    Input format: "[00:01] Therapist: Hello..."
    Output format: "[S0] [00:01] Therapist: Hello..."
    """
    lines = transcript_content.strip().splitlines()
    formatted: list[str] = []
    idx = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        formatted.append(f"[S{idx}] {stripped}")
        idx += 1
    return "\n".join(formatted)


def build_attribution_prompt(
    claims: dict[ClaimKey, SOAPSentence],
    transcript_with_ids: str,
) -> str:
    """Build the LLM prompt for source attribution (Call 2)."""
    numbered_claims: list[str] = []
    key_order: list[str] = []
    for i, (key, sentence) in enumerate(claims.items(), start=1):
        numbered_claims.append(f'{i}. [{key}]: "{sentence.text}"')
        key_order.append(key)

    claims_text = "\n".join(numbered_claims)

    return f"""Given the following therapy session transcript and AI-generated SOAP note claims, \
identify which transcript segments support each claim.

For each claim, return the segment indices (the number after S, e.g., 0 for [S0]) \
that contain evidence supporting the claim. If no clear transcript support exists, \
return an empty array.

Transcript:
{transcript_with_ids}

Claims:
{claims_text}

Return ONLY valid JSON mapping claim number (as string) to array of segment indices:
{{"1": [0, 3], "2": [1, 8], ...}}"""


def parse_attribution_response(
    response_text: str,
    claims: dict[ClaimKey, SOAPSentence],
    max_segment_id: int | None = None,
) -> None:
    """Parse LLM attribution response and merge segment IDs into SOAPSentence objects.

    Modifies the SOAPSentence objects in-place.
    Handles LLM returning segment IDs as either integers or strings.
    Rejects out-of-bounds segment IDs when max_segment_id is provided.
    """
    json_str = _extract_json(response_text)
    if not json_str:
        return

    try:
        mapping: dict[str, list[int | str]] = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        return

    key_list = list(claims.keys())
    for num_str, segment_ids in mapping.items():
        try:
            idx = int(num_str) - 1  # 1-based to 0-based
        except ValueError:
            continue
        if 0 <= idx < len(key_list):
            key = key_list[idx]
            claims[key].source_segment_ids = _parse_segment_ids(segment_ids, max_segment_id)


def _extract_json(text: str) -> str | None:
    """Extract JSON object from LLM response, handling markdown code blocks."""
    # Strip markdown code fences first
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]

    # Find the outermost { ... } allowing nested content
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]
    return None


def _parse_segment_ids(raw_ids: list[int | str], max_segment_id: int | None = None) -> list[int]:
    """Parse segment IDs from LLM response, handling both int and string formats.

    Rejects negative IDs and IDs exceeding max_segment_id (LLM hallucinations).
    """
    parsed: list[int] = []
    for sid in raw_ids:
        if isinstance(sid, int):
            val = sid
        elif isinstance(sid, str):
            try:
                val = int(sid)
            except ValueError:
                continue
        else:
            continue
        if val < 0:
            continue
        if max_segment_id is not None and val > max_segment_id:
            continue
        parsed.append(val)
    return parsed


def merge_attribution_into_soap(
    soap_note: SOAPNote,
    attribution: dict[ClaimKey, list[int]],
) -> None:
    """Merge attribution results directly into SOAPNote's SOAPSentence objects.

    This is a convenience function that takes a pre-parsed mapping.
    """
    claims = build_claims_from_soap(soap_note)
    for key, segment_ids in attribution.items():
        if key in claims:
            claims[key].source_segment_ids = segment_ids
