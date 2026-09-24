# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Intake: the CPT 90791 biopsychosocial assessment.

A patient-context format, scoped to the client rather than a session.
The schema is grounded against CMS MBPM Ch. 15 section 160, Joint
Commission EC/PC standards, CARF 1.H. and the APA Record Keeping
Guidelines. Fields with no ``ai_hint`` are clinician-authored and are
never populated by generation.
"""

from __future__ import annotations

from .registry import NoteFieldDef, NoteSectionDef, NoteTypeDefinition

INTAKE_DEFINITION = NoteTypeDefinition(
    key="intake",
    label="Intake",
    description=(
        "First-session biopsychosocial assessment, billed as CPT 90791. "
        "Patient-context document supporting medical necessity for the "
        "course of treatment. Required by CMS MBPM Ch. 15 §160, Joint "
        "Commission EC/PC standards, and CARF 1.H. before ongoing "
        "therapy is reimbursable."
    ),
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="identification",
            label="Identification",
            fields=(
                NoteFieldDef(
                    key="datetime_of_service",
                    label="Date/Time of Service",
                    kind="text",
                ),
                NoteFieldDef(
                    key="duration_minutes",
                    label="Duration (minutes)",
                    kind="text",
                ),
                NoteFieldDef(
                    key="modality",
                    label="Modality",
                    kind="text",
                ),
                NoteFieldDef(
                    key="referral_source",
                    label="Referral Source",
                    kind="text",
                ),
                NoteFieldDef(
                    key="referral_reason",
                    label="Referral Reason",
                    kind="text",
                    ai_hint=(
                        "Why the patient was referred or sought care, in their own words "
                        "where possible. Usually surfaces in the opening minutes of the session."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="presenting",
            label="Presenting",
            fields=(
                NoteFieldDef(
                    key="chief_complaint",
                    label="Chief Complaint",
                    kind="text",
                    ai_hint=(
                        "The primary problem that brought the patient in today. Use the "
                        "patient's own words in quotes when they're clear."
                    ),
                ),
                NoteFieldDef(
                    key="history_of_present_illness",
                    label="History of Present Illness",
                    kind="text",
                    ai_hint=(
                        "Onset, duration, severity, triggers, course, and prior episodes "
                        "of the chief complaint as the patient describes them."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="history",
            label="History",
            fields=(
                NoteFieldDef(
                    key="psychiatric_history",
                    label="Psychiatric History",
                    kind="text",
                    ai_hint=(
                        "Prior mental-health diagnoses, treatments, hospitalizations, and "
                        "medications as the patient reports them. Leave empty if not discussed."
                    ),
                ),
                NoteFieldDef(
                    key="medical_history",
                    label="Medical History",
                    kind="text",
                    ai_hint=(
                        "Relevant medical conditions, surgeries, and chronic illnesses the "
                        "patient mentions. Leave empty if not discussed."
                    ),
                ),
                NoteFieldDef(
                    key="current_medications",
                    label="Current Medications",
                    kind="list",
                    ai_hint=(
                        "Each medication the patient reports, with dose and prescriber when "
                        "stated. One item per medication."
                    ),
                ),
                NoteFieldDef(
                    key="substance_use_history",
                    label="Substance Use History",
                    kind="text",
                    ai_hint=(
                        "Alcohol, tobacco, cannabis, and other substance use as the patient "
                        "describes it. Populate only when explicitly discussed."
                    ),
                ),
                NoteFieldDef(
                    key="family_history",
                    label="Family History",
                    kind="text",
                    ai_hint=(
                        "Family history of mental-health or substance-use conditions as the "
                        "patient reports it. Populate only when explicitly discussed."
                    ),
                ),
                NoteFieldDef(
                    key="developmental_history",
                    label="Developmental History",
                    kind="text",
                    ai_hint=(
                        "Developmental milestones, early childhood, and educational history "
                        "the patient describes. Populate only when discussed."
                    ),
                ),
                NoteFieldDef(
                    key="trauma_history",
                    label="Trauma History",
                    kind="text",
                    ai_hint=(
                        "Traumatic experiences the patient discloses. Use the patient's own "
                        "framing; do not infer trauma from absence. Often deferred from intake."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="social",
            label="Social",
            fields=(
                NoteFieldDef(
                    key="social_history",
                    label="Social History",
                    kind="text",
                    ai_hint=(
                        "Housing, employment, education, finances, legal involvement, "
                        "military service, and cultural or spiritual identity as discussed."
                    ),
                ),
                NoteFieldDef(
                    key="relationships_supports",
                    label="Relationships & Supports",
                    kind="text",
                    ai_hint=(
                        "Family system, partners, friends, and other support network as the "
                        "patient describes them."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="mse",
            label="Mental Status Exam",
            fields=(
                NoteFieldDef(
                    key="appearance_behavior",
                    label="Appearance & Behavior",
                    kind="text",
                ),
                NoteFieldDef(
                    key="speech",
                    label="Speech",
                    kind="text",
                    ai_hint=(
                        "Audible features only: rate, rhythm, volume, fluency. "
                        "Do not infer; report what is heard."
                    ),
                ),
                NoteFieldDef(
                    key="mood_affect",
                    label="Mood & Affect",
                    kind="text",
                    ai_hint=(
                        "Self-reported mood in quotes where possible; affect inferred from "
                        "prosody and content (e.g. congruent, blunted, labile)."
                    ),
                ),
                NoteFieldDef(
                    key="thought_process_content",
                    label="Thought Process & Content",
                    kind="text",
                    ai_hint=(
                        "Linear vs. tangential is audible. Suicidal or homicidal ideation, "
                        "delusions, or obsessions only when the patient explicitly reports them."
                    ),
                ),
                NoteFieldDef(
                    key="perception",
                    label="Perception",
                    kind="text",
                    ai_hint=(
                        "Hallucinations, derealization, or depersonalization only when the "
                        "patient explicitly reports them."
                    ),
                ),
                NoteFieldDef(
                    key="cognition",
                    label="Cognition",
                    kind="text",
                ),
                NoteFieldDef(
                    key="insight_judgment",
                    label="Insight & Judgment",
                    kind="text",
                ),
            ),
        ),
        NoteSectionDef(
            key="risk",
            label="Risk",
            fields=(
                NoteFieldDef(
                    key="suicide_risk",
                    label="Suicide Risk",
                    kind="text",
                ),
                NoteFieldDef(
                    key="homicide_risk",
                    label="Homicide Risk",
                    kind="text",
                ),
                NoteFieldDef(
                    key="self_harm_risk",
                    label="Self-Harm Risk",
                    kind="text",
                ),
                NoteFieldDef(
                    key="abuse_neglect",
                    label="Abuse / Neglect",
                    kind="text",
                ),
                NoteFieldDef(
                    key="protective_factors",
                    label="Protective Factors",
                    kind="text",
                ),
            ),
        ),
        NoteSectionDef(
            key="strengths",
            label="Strengths",
            fields=(
                NoteFieldDef(
                    key="strengths",
                    label="Strengths",
                    kind="list",
                    ai_hint=(
                        "Personal strengths, resources, and protective qualities the patient "
                        "names or demonstrates. One item per strength."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="assessment",
            label="Assessment",
            fields=(
                NoteFieldDef(
                    key="dsm5_diagnosis",
                    label="DSM-5 Diagnosis",
                    kind="list",
                ),
                NoteFieldDef(
                    key="case_formulation",
                    label="Case Formulation",
                    kind="text",
                ),
            ),
        ),
        NoteSectionDef(
            key="plan",
            label="Plan",
            fields=(
                NoteFieldDef(
                    key="initial_recommendations",
                    label="Initial Recommendations",
                    kind="text",
                    ai_hint=(
                        "Recommended level of care, modality, frequency, and any referrals "
                        "as discussed in session."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="consents",
            label="Consents",
            fields=(
                NoteFieldDef(
                    key="consent_to_treatment_signed",
                    label="Consent to Treatment Signed",
                    kind="text",
                ),
                NoteFieldDef(
                    key="roi_signed",
                    label="Releases of Information Signed",
                    kind="text",
                ),
                NoteFieldDef(
                    key="telehealth_consent_signed",
                    label="Telehealth Consent Signed",
                    kind="text",
                ),
            ),
        ),
        NoteSectionDef(
            key="signature",
            label="Signature",
            fields=(
                NoteFieldDef(
                    key="clinician_signature",
                    label="Clinician Signature",
                    kind="text",
                ),
                NoteFieldDef(
                    key="signed_at",
                    label="Signed At",
                    kind="text",
                ),
                NoteFieldDef(
                    key="supervisor_cosignature_required",
                    label="Supervisor Co-signature Required",
                    kind="text",
                ),
                NoteFieldDef(
                    key="supervisor_signature",
                    label="Supervisor Signature",
                    kind="text",
                ),
                NoteFieldDef(
                    key="supervisor_signed_at",
                    label="Supervisor Signed At",
                    kind="text",
                ),
            ),
        ),
    ),
)
