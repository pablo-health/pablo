# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Safety Plan: the Stanley-Brown Safety Planning Intervention.

A strict six-step structure (Stanley & Brown 2012) endorsed by the
VA/DoD CPG, SAMHSA, Zero Suicide and Joint Commission NPSG 15.01.01.
Surveyors check the Step 3 (distractions) versus Step 4 (help)
distinction and require means restriction to be specific, not
"discussed." Generation populates a step only when the transcript
explicitly supports it; missing answers stay empty.
"""

from __future__ import annotations

from .registry import NoteFieldDef, NoteSectionDef, NoteTypeDefinition

SAFETY_PLAN_DEFINITION = NoteTypeDefinition(
    key="safety_plan",
    label="Safety Plan",
    description=(
        "Stanley-Brown Safety Planning Intervention (Stanley & Brown 2012). "
        "Endorsed by VA/DoD CPG, SAMHSA, Zero Suicide Institute, and Joint "
        "Commission NPSG 15.01.01. Six strict steps — surveyors check the "
        "Step 3 (distractions) vs. Step 4 (help-seeking) distinction, and "
        "require means-restriction to be specific, not the word 'discussed.' "
        "Replaces the contraindicated 'no-suicide contract' (Rudd et al. 2006); "
        "do not add one. Transcript-mode rule applies: the LLM only populates "
        "a step's field when the recording explicitly supports it — warning "
        "signs and coping strategies must come from the patient, never invented "
        "from absence."
    ),
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="warning_signs",
            label="Step 1 — Warning Signs",
            fields=(
                NoteFieldDef(
                    key="warning_signs",
                    label="Warning Signs",
                    kind="list",
                    ai_hint=(
                        "Personal warning signs the patient identifies — thoughts, "
                        "images, mood states, situations, or behaviors that signal "
                        "a developing crisis. Use the patient's own language. One "
                        "item per sign. Populate only when the patient names them."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="internal_coping",
            label="Step 2 — Internal Coping Strategies",
            fields=(
                NoteFieldDef(
                    key="internal_coping_strategies",
                    label="Internal Coping Strategies",
                    kind="list",
                    ai_hint=(
                        "Things the patient can do alone — without contacting "
                        "another person — to take their mind off the crisis "
                        "(e.g. exercise, music, prayer, a specific routine). "
                        "One strategy per item. Populate only what the patient names."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="distractions",
            label="Step 3 — People & Settings for Distraction",
            fields=(
                NoteFieldDef(
                    key="distracting_people_settings",
                    label="People & Settings for Distraction",
                    kind="list",
                    ai_hint=(
                        "People or social settings the patient can use as a "
                        "distraction from suicidal thoughts. NOT for help-seeking "
                        "— that is Step 4. Do not include phone numbers here; "
                        "names of people or places only. Populate only what the "
                        "patient names."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="help_contacts",
            label="Step 4 — People to Ask for Help",
            fields=(
                NoteFieldDef(
                    key="help_people",
                    label="People to Ask for Help",
                    kind="list",
                    ai_hint=(
                        "People the patient will contact for help during a crisis "
                        "— distinct from Step 3 distractions. Each item should "
                        "capture the person's name and phone number as the "
                        "patient gives them. Populate only what the patient names."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="professionals",
            label="Step 5 — Professionals & Agencies",
            fields=(
                NoteFieldDef(
                    key="professional_contacts",
                    label="Professional & Agency Contacts",
                    kind="list",
                    ai_hint=(
                        "Clinicians, urgent-care lines, and crisis services the "
                        "patient will contact. Each item should capture name, "
                        "role, and phone number. The 988 Suicide & Crisis "
                        "Lifeline must be present; add it if the patient did "
                        "not name it."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="means_restriction",
            label="Step 6 — Means Restriction",
            fields=(
                NoteFieldDef(
                    key="firearms_present",
                    label="Firearms Present",
                    kind="text",
                ),
                NoteFieldDef(
                    key="firearms_plan",
                    label="Firearms Restriction Plan",
                    kind="text",
                    ai_hint=(
                        "The specific plan for restricting access to firearms — "
                        "who will store them, where, by when. Surveyors reject "
                        "the word 'discussed'; the plan must be concrete. "
                        "Populate only what the patient and clinician agreed to "
                        "in the recording."
                    ),
                ),
                NoteFieldDef(
                    key="medications_secured",
                    label="Medications Secured",
                    kind="text",
                ),
                NoteFieldDef(
                    key="medications_plan",
                    label="Medications Restriction Plan",
                    kind="text",
                    ai_hint=(
                        "The specific plan for securing medications — lockbox, "
                        "supply limits, who holds the key. Concrete, not "
                        "'discussed.' Populate only what was explicitly agreed."
                    ),
                ),
                NoteFieldDef(
                    key="other_means_plan",
                    label="Other Means Restriction Plan",
                    kind="text",
                    ai_hint=(
                        "Any other lethal means addressed (sharps, ligatures, "
                        "vehicles, location access) and the specific restriction "
                        "plan. Populate only what was explicitly discussed."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="reasons_for_living",
            label="Reasons for Living (optional)",
            fields=(
                NoteFieldDef(
                    key="reasons_for_living",
                    label="Reasons for Living",
                    kind="list",
                    ai_hint=(
                        "Patient-named reasons for living (Linehan adjunct used by "
                        "many SPI programs). One reason per item, in the patient's "
                        "own framing. Populate only what the patient names."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="status",
            label="Status",
            fields=(
                NoteFieldDef(
                    key="risk_level",
                    label="Risk Level",
                    kind="text",
                ),
                NoteFieldDef(
                    key="follow_up_at",
                    label="Follow-up At",
                    kind="text",
                ),
            ),
        ),
        NoteSectionDef(
            key="acknowledgment",
            label="Acknowledgment",
            fields=(
                NoteFieldDef(
                    key="patient_collaboration_acknowledged",
                    label="Patient Collaboration Acknowledged",
                    kind="text",
                ),
                NoteFieldDef(
                    key="patient_signature_acknowledged",
                    label="Patient Signature Acknowledged",
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
            ),
        ),
    ),
)
