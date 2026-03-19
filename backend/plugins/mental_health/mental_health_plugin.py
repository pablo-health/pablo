"""
Mental Health SOAP Notes Plugin

Generates structured SOAP notes (Subjective, Objective, Assessment, Plan)
from mental health therapy session transcripts. Designed for licensed mental
health professionals to streamline clinical documentation while maintaining
HIPAA compliance and professional standards.
"""

import json
from pathlib import Path
from typing import Any

from meeting_transcription.pipeline.core import BasePromptablePlugin


class MentalHealthPlugin(BasePromptablePlugin):
    """
    Mental Health SOAP Notes Plugin

    Generates comprehensive SOAP notes from therapy session transcripts.
    Extracts clinical observations, assessments, and treatment plans in a
    structured format suitable for electronic health records (EHR).
    """

    def __init__(self):
        """Initialize plugin with default settings."""
        self.include_verbatim_quotes = True
        self.risk_assessment_required = True
        self.hipaa_compliant_mode = True

    # ========================================================================
    # TranscriptPlugin Protocol Implementation
    # ========================================================================

    @property
    def name(self) -> str:
        """Plugin identifier (lowercase, no spaces)."""
        return "mental_health"

    @property
    def display_name(self) -> str:
        """Human-readable name shown in UI."""
        return "Mental Health SOAP Notes"

    @property
    def description(self) -> str:
        """Plugin description shown in UI."""
        return (
            "Generates structured SOAP notes (Subjective, Objective, Assessment, Plan) "
            "from mental health therapy session transcripts"
        )

    @property
    def metadata_schema(self) -> dict[str, Any]:
        """Define metadata fields needed at meeting creation."""
        return {
            "client_name": {"type": "string", "description": "Client name or identifier"},
            "session_date": {"type": "string", "description": "Date of therapy session"},
            "session_number": {"type": "string", "description": "Session number or identifier"},
            "therapist_name": {"type": "string", "description": "Therapist/clinician name"},
            "diagnosis": {
                "type": "string",
                "description": "Current diagnosis/diagnoses (optional)",
            },
        }

    @property
    def settings_schema(self) -> dict[str, Any]:
        """Define user-configurable settings."""
        return {
            "include_verbatim_quotes": {
                "type": "boolean",
                "default": True,
                "description": "Include direct quotes from client in subjective section",
            },
            "risk_assessment_required": {
                "type": "boolean",
                "default": True,
                "description": "Always include risk assessment in notes",
            },
            "hipaa_compliant_mode": {
                "type": "boolean",
                "default": True,
                "description": "Ensure HIPAA-compliant language and avoid identifying details",
            },
        }

    def configure(self, settings: dict[str, Any]) -> None:
        """Apply settings to plugin instance."""
        self.include_verbatim_quotes = settings.get("include_verbatim_quotes", True)
        self.risk_assessment_required = settings.get("risk_assessment_required", True)
        self.hipaa_compliant_mode = settings.get("hipaa_compliant_mode", True)

    # ========================================================================
    # BasePromptablePlugin Implementation (REQUIRED)
    # ========================================================================

    def get_extraction_prompt(self, transcript_text: str, metadata: dict[str, Any]) -> str:
        """
        Generate the LLM extraction prompt.

        Args:
            transcript_text: Pre-formatted transcript with timestamps
                Format: "[00:05] Speaker: text\\n[00:10] Speaker: text\\n..."
            metadata: Meeting metadata from metadata_schema

        Returns:
            Complete prompt string for LLM
        """
        client_name = metadata.get("client_name", "Client")
        session_date = metadata.get("session_date", "")
        therapist_name = metadata.get("therapist_name", "Therapist")
        diagnosis = metadata.get("diagnosis", "")

        quotes_instruction = ""
        if self.include_verbatim_quotes:
            quotes_instruction = (
                "Include relevant direct quotes from the client to support your observations."
            )

        hipaa_instruction = ""
        if self.hipaa_compliant_mode:
            hipaa_instruction = """
IMPORTANT - HIPAA COMPLIANCE:
- Do NOT include specific identifying details (full names, addresses, phone numbers, etc.)
- Use general terms and clinical language
- Focus on clinical observations and therapeutic content
"""

        risk_instruction = ""
        if self.risk_assessment_required:
            risk_instruction = (
                "Risk assessment is REQUIRED - always evaluate and document safety concerns "
                "including suicide risk, self-harm, and harm to others."
            )

        prompt = f"""You are a licensed mental health professional creating a SOAP note
from a therapy session transcript.

# Session Information
- Client: {client_name}
- Date: {session_date}
- Therapist: {therapist_name}
{f"- Diagnosis: {diagnosis}" if diagnosis else ""}

{hipaa_instruction}

# Task
Create a comprehensive SOAP note (Subjective, Objective, Assessment, Plan)
from this therapy session transcript.

# SOAP Note Structure

**SUBJECTIVE** - What the client reports:
- Chief complaint: Primary concern or reason for session
- Mood/Affect: Client's self-reported emotional state
- Symptoms: Difficulties or symptoms reported by client
- Client narrative: Summary of client's story in their perspective
{quotes_instruction}

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
{risk_instruction}

**PLAN** - Treatment and next steps:
- Interventions used: Therapeutic techniques or modalities used in session
  (CBT, DBT, motivational interviewing, etc.)
- Homework assignments: Tasks or exercises for client to complete
- Next steps: Action items and follow-up plans
- Next session: Plan for next session (timing, focus areas)

# Transcript
{transcript_text}

# Instructions
1. Read through the entire transcript carefully
2. Identify who is the client vs. therapist based on context
3. Extract information for each SOAP section
4. Use professional, clinical language
5. Be thorough but concise
6. Ensure all required fields are completed
7. Base observations only on what is evident in the transcript
"""
        return prompt

    def get_response_schema(self) -> dict[str, Any] | None:
        """
        Define JSON schema for structured output.

        Returns JSON schema dict or None for free-form text.
        """
        return {
            "type": "object",
            "properties": {
                "subjective": {
                    "type": "object",
                    "description": (
                        "Subjective information: client's reported experiences, "
                        "feelings, and concerns"
                    ),
                    "properties": {
                        "chief_complaint": {
                            "type": "string",
                            "description": (
                                "Primary reason for the session or main concern expressed"
                            ),
                        },
                        "mood_affect": {
                            "type": "string",
                            "description": "Client's self-reported mood and emotional state",
                        },
                        "symptoms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Symptoms or difficulties reported by the client",
                        },
                        "client_narrative": {
                            "type": "string",
                            "description": "Summary of the client's story and perspective",
                        },
                    },
                    "required": ["chief_complaint", "mood_affect", "client_narrative"],
                },
                "objective": {
                    "type": "object",
                    "description": "Objective observations: clinician's observations of the client",
                    "properties": {
                        "appearance": {
                            "type": "string",
                            "description": "Observable appearance and presentation",
                        },
                        "behavior": {
                            "type": "string",
                            "description": "Observable behaviors during session",
                        },
                        "speech": {
                            "type": "string",
                            "description": "Speech patterns (rate, tone, coherence)",
                        },
                        "thought_process": {
                            "type": "string",
                            "description": "Organization and flow of thoughts",
                        },
                        "affect_observed": {
                            "type": "string",
                            "description": "Clinician's observation of emotional expression",
                        },
                    },
                    "required": ["behavior", "affect_observed"],
                },
                "assessment": {
                    "type": "object",
                    "description": ("Assessment: clinical interpretation and progress evaluation"),
                    "properties": {
                        "clinical_impression": {
                            "type": "string",
                            "description": ("Overall clinical impression and formulation"),
                        },
                        "progress": {
                            "type": "string",
                            "description": "Progress toward treatment goals",
                        },
                        "risk_assessment": {
                            "type": "string",
                            "description": (
                                "Assessment of safety concerns "
                                "(suicide, self-harm, harm to others)"
                            ),
                        },
                        "functioning_level": {
                            "type": "string",
                            "description": (
                                "Current level of functioning "
                                "(social, occupational, daily activities)"
                            ),
                        },
                    },
                    "required": ["clinical_impression", "risk_assessment"],
                },
                "plan": {
                    "type": "object",
                    "description": "Plan: treatment interventions and next steps",
                    "properties": {
                        "interventions_used": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Therapeutic interventions or techniques used in session"
                            ),
                        },
                        "homework_assignments": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Tasks or exercises assigned to client",
                        },
                        "next_steps": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Action items and follow-up plans",
                        },
                        "next_session": {
                            "type": "string",
                            "description": "Plan for next session and frequency of care",
                        },
                    },
                    "required": ["interventions_used", "next_steps"],
                },
            },
            "required": ["subjective", "objective", "assessment", "plan"],
        }

    def process_llm_response(  # noqa: PLR0915
        self, llm_response: dict[str, Any], output_dir: str, metadata: dict[str, Any]
    ) -> dict[str, str]:
        """
        Process LLM response into output files.

        Args:
            llm_response: Parsed JSON from LLM (matches get_response_schema)
            output_dir: Directory to write output files
            metadata: Meeting metadata

        Returns:
            Dict of output file paths
        """
        outputs = {}

        # Save as JSON
        json_path = Path(output_dir) / "soap_note.json"
        with json_path.open("w") as f:
            json.dump(llm_response, f, indent=2)
        outputs["soap_note_json"] = str(json_path)

        # Save as formatted Markdown
        md_path = Path(output_dir) / "soap_note.md"
        with md_path.open("w") as f:
            # Header
            f.write("# SOAP Note - Therapy Session\n\n")
            f.write(f"**Client:** {metadata.get('client_name', 'N/A')}  \n")
            f.write(f"**Date:** {metadata.get('session_date', 'N/A')}  \n")
            f.write(f"**Session:** {metadata.get('session_number', 'N/A')}  \n")
            f.write(f"**Therapist:** {metadata.get('therapist_name', 'N/A')}  \n")
            if metadata.get("diagnosis"):
                f.write(f"**Diagnosis:** {metadata.get('diagnosis')}  \n")
            f.write("\n---\n\n")

            # Subjective
            subjective = llm_response.get("subjective", {})
            f.write("## S - Subjective\n\n")
            f.write(f"**Chief Complaint:** {subjective.get('chief_complaint', 'N/A')}\n\n")
            f.write(f"**Mood/Affect (Self-Reported):** {subjective.get('mood_affect', 'N/A')}\n\n")

            symptoms = subjective.get("symptoms", [])
            if symptoms:
                f.write("**Symptoms Reported:**\n")
                for symptom in symptoms:
                    f.write(f"- {symptom}\n")
                f.write("\n")

            f.write(f"**Client Narrative:**\n{subjective.get('client_narrative', 'N/A')}\n\n")

            # Objective
            objective = llm_response.get("objective", {})
            f.write("## O - Objective\n\n")
            if objective.get("appearance"):
                f.write(f"**Appearance:** {objective.get('appearance')}\n\n")
            f.write(f"**Behavior:** {objective.get('behavior', 'N/A')}\n\n")
            if objective.get("speech"):
                f.write(f"**Speech:** {objective.get('speech')}\n\n")
            if objective.get("thought_process"):
                f.write(f"**Thought Process:** {objective.get('thought_process')}\n\n")
            f.write(f"**Affect (Observed):** {objective.get('affect_observed', 'N/A')}\n\n")

            # Assessment
            assessment = llm_response.get("assessment", {})
            f.write("## A - Assessment\n\n")
            f.write(f"**Clinical Impression:**\n{assessment.get('clinical_impression', 'N/A')}\n\n")
            f.write(f"**Progress:** {assessment.get('progress', 'N/A')}\n\n")
            f.write(f"**Risk Assessment:** {assessment.get('risk_assessment', 'N/A')}\n\n")
            if assessment.get("functioning_level"):
                f.write(f"**Functioning Level:** {assessment.get('functioning_level')}\n\n")

            # Plan
            plan = llm_response.get("plan", {})
            f.write("## P - Plan\n\n")

            interventions = plan.get("interventions_used", [])
            if interventions:
                f.write("**Interventions Used:**\n")
                for intervention in interventions:
                    f.write(f"- {intervention}\n")
                f.write("\n")

            homework = plan.get("homework_assignments", [])
            if homework:
                f.write("**Homework Assignments:**\n")
                for hw in homework:
                    f.write(f"- {hw}\n")
                f.write("\n")

            next_steps = plan.get("next_steps", [])
            if next_steps:
                f.write("**Next Steps:**\n")
                for step in next_steps:
                    f.write(f"- {step}\n")
                f.write("\n")

            f.write(f"**Next Session:** {plan.get('next_session', 'N/A')}\n")

        outputs["soap_note_md"] = md_path

        return outputs

    # ========================================================================
    # BasePromptablePlugin Optional Overrides
    # ========================================================================

    def _format_transcript_for_prompt(self, chunked_data: dict[str, Any]) -> str:
        """Format transcript with [Sn] segment indices for source linking.

        Override of BasePromptablePlugin._format_transcript_for_prompt().
        Prepends each line with [S0], [S1], ... so the LLM and the attribution
        service can reference specific transcript segments.
        """
        lines = []
        idx = 0
        for chunk in chunked_data["chunks"]:
            for segment in chunk.get("segments", []):
                speaker = segment["participant"]["name"]
                text = segment["text"]
                timestamp = segment["start_timestamp"]["relative"]
                minutes = int(timestamp // 60)
                seconds = int(timestamp % 60)
                lines.append(f"[S{idx}] [{minutes:02d}:{seconds:02d}] {speaker}: {text}")
                idx += 1
        return "\n".join(lines)

    def get_temperature(self) -> float:
        """Return temperature for LLM sampling. Default: 0.7"""
        return 0.3  # Lower temperature for more consistent, clinical output

    def get_max_tokens(self) -> int:
        """Return max tokens for LLM response. Default: 8000"""
        return 4000  # SOAP notes should be comprehensive but concise
