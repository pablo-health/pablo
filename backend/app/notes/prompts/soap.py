# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Hand-tuned SOAP-generation prompt.

Migrated verbatim from the legacy
``backend/plugins/mental_health/mental_health_plugin.py:get_extraction_prompt``
when SOAP joined the registry-driven path (THERAPY-71d5 / 9ijg).

The original plugin owned three boolean settings
(``include_verbatim_quotes``, ``risk_assessment_required``,
``hipaa_compliant_mode``) and conditionally injected paragraphs based on
them. All three were always set to ``True`` in production by
the legacy ``MeetingTranscriptionNoteService._generate_soap_via_plugin``
(deleted in the same PR), so the
migrated prompt has those branches baked in — no runtime knobs.

The transcript is fed in already normalized to canonical
``[MM:SS] Speaker: text`` lines (and then prefixed with ``[Sn]`` segment
ids by :func:`source_attribution_service.format_transcript_with_segment_ids`)
so the Call-2 source-attribution pass can map each generated sentence
back to the original transcript segments.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ...models import Patient, Transcript
    from ..registry import NoteTypeDefinition


SOAP_SYSTEM_PROMPT = (
    "You are a licensed mental health professional creating a SOAP note from "
    "a therapy session transcript. Use professional, clinical language. Be "
    "thorough but concise. Base your observations only on what is evident "
    "in the transcript."
)


def build_soap_prompt(
    _definition: NoteTypeDefinition,
    transcript: Transcript,
    patient: Patient,
    session_date: datetime,
) -> str:
    """Return the legacy SOAP user prompt with the transcript embedded.

    The system-prompt half is :data:`SOAP_SYSTEM_PROMPT`; callers pass it
    to the structured gateway alongside this user prompt.
    """
    # Local imports — these two modules transitively pull in
    # ``app.services``, which pulls in ``app.notes.builtin`` (which
    # imports *us*). Importing at module load creates a cycle: this is
    # the cycle-breaking idiom Pablo already uses in
    # ``ehr_navigation_service`` and ``chat_llm_gateway``.
    from ...services.source_attribution_service import (  # noqa: PLC0415
        format_transcript_with_segment_ids,
    )
    from ..transcript_normalize import (  # noqa: PLC0415
        normalize_transcript_to_canonical_lines,
    )

    canonical = normalize_transcript_to_canonical_lines(transcript.content, transcript.format)
    indexed = format_transcript_with_segment_ids(canonical)
    session_date_str = session_date.isoformat().split("T", maxsplit=1)[0]
    diagnosis_line = f"- Diagnosis: {patient.diagnosis}" if patient.diagnosis else ""

    return f"""# Session Information
- Client: the client
- Date: {session_date_str}
{diagnosis_line}

IMPORTANT - HIPAA COMPLIANCE:
- Do NOT include specific identifying details (full names, addresses, phone numbers, etc.)
- Use general terms and clinical language
- Focus on clinical observations and therapeutic content

# Task
Create a comprehensive SOAP note (Subjective, Objective, Assessment, Plan)
from this therapy session transcript.

# SOAP Note Structure

**SUBJECTIVE** - What the client reports:
- Chief complaint: Primary concern or reason for session
- Mood/Affect: Client's self-reported emotional state
- Symptoms: Difficulties or symptoms reported by client
- Client narrative: Summary of client's story in their perspective
Include relevant direct quotes from the client to support your observations.

**OBJECTIVE** - What you observe:
- Appearance: Observable presentation (grooming, dress, etc.)
- Behavior: Observable behaviors during session (eye contact, posture,
  engagement)
- Speech: Rate, tone, volume, coherence of speech
- Thought process: Organization, logical flow, tangentiality
- Affect observed: Your observation of emotional expression (congruent/
  incongruent, range, appropriateness)

**ASSESSMENT** - Your clinical interpretation:
- Clinical impression: Overall formulation and understanding of client's presentation
- Progress: Progress toward treatment goals (improving, stable, declining)
- Risk assessment: Safety concerns (suicide, self-harm, harm to others, substance use)
- Functioning level: Current functioning in social, occupational, and daily activities
Risk assessment is REQUIRED - always evaluate and document safety concerns \
including suicide risk, self-harm, and harm to others.

**PLAN** - Treatment and next steps:
- Interventions used: Therapeutic techniques or modalities used in session
  (CBT, DBT, motivational interviewing, etc.)
- Homework assignments: Tasks or exercises for client to complete
- Next steps: Action items and follow-up plans
- Next session: Plan for next session (timing, focus areas)

# Transcript
{indexed}

# Instructions
1. Read through the entire transcript carefully
2. Identify who is the client vs. therapist based on context
3. Extract information for each SOAP section
4. Use professional, clinical language
5. Be thorough but concise
6. Ensure all required fields are completed
7. Base observations only on what is evident in the transcript
"""


__all__ = ["SOAP_SYSTEM_PROMPT", "build_soap_prompt"]
