// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SubFieldEditor Component
 *
 * Renders individual editable sub-fields for a SOAP section.
 * Text fields get a single textarea; list fields get a textarea
 * where each line becomes an array item.
 */

import type {
  SOAPSentence,
  SubjectiveNote,
  ObjectiveNote,
  AssessmentNote,
  PlanNote,
} from "@/types/sessions"

type SectionData = SubjectiveNote | ObjectiveNote | AssessmentNote | PlanNote

interface SubFieldDef {
  key: string
  label: string
  type: "text" | "list"
}

export const SECTION_SUBFIELDS: Record<string, SubFieldDef[]> = {
  subjective: [
    { key: "chief_complaint", label: "Chief Complaint", type: "text" },
    { key: "mood_affect", label: "Mood/Affect", type: "text" },
    { key: "symptoms", label: "Symptoms", type: "list" },
    { key: "client_narrative", label: "Client Narrative", type: "text" },
  ],
  objective: [
    { key: "appearance", label: "Appearance", type: "text" },
    { key: "behavior", label: "Behavior", type: "text" },
    { key: "speech", label: "Speech", type: "text" },
    { key: "thought_process", label: "Thought Process", type: "text" },
    { key: "affect_observed", label: "Affect Observed", type: "text" },
  ],
  assessment: [
    { key: "clinical_impression", label: "Clinical Impression", type: "text" },
    { key: "progress", label: "Progress", type: "text" },
    { key: "risk_assessment", label: "Risk Assessment", type: "text" },
    { key: "functioning_level", label: "Functioning Level", type: "text" },
  ],
  plan: [
    { key: "interventions_used", label: "Interventions Used", type: "list" },
    { key: "homework_assignments", label: "Homework Assignments", type: "list" },
    { key: "next_steps", label: "Next Steps", type: "list" },
    { key: "next_session", label: "Next Session", type: "text" },
  ],
}

function sentence(text: string): SOAPSentence {
  return { text, source_segment_ids: [], confidence_score: 0.0, confidence_level: "", possible_match_segment_ids: [], signal_used: "" }
}

function sentenceList(items: string[]): SOAPSentence[] {
  return items.map((t) => sentence(t))
}

function listToText(items: SOAPSentence[] | null): string {
  if (!items) return ""
  return items.map((s) => s.text).join("\n")
}

function textToList(text: string): SOAPSentence[] | null {
  const items = text.split("\n").map((s) => s.trim()).filter(Boolean)
  return items.length > 0 ? sentenceList(items) : null
}

interface SubFieldEditorProps {
  sectionKey: string
  data: SectionData
  onChange: (updated: SectionData) => void
}

export function SubFieldEditor({ sectionKey, data, onChange }: SubFieldEditorProps) {
  const fields = SECTION_SUBFIELDS[sectionKey]
  if (!fields) return null

  const record = data as unknown as Record<string, SOAPSentence | SOAPSentence[] | null>

  const handleChange = (fieldKey: string, fieldType: string, value: string) => {
    const newValue = fieldType === "list" ? textToList(value) : sentence(value)
    onChange({ ...data, [fieldKey]: newValue } as SectionData)
  }

  return (
    <div className="space-y-4">
      {fields.map((f) => {
        const rawValue = record[f.key]
        const displayValue = f.type === "list"
          ? listToText(rawValue as SOAPSentence[] | null)
          : (rawValue as SOAPSentence | null)?.text ?? ""

        return (
          <div key={f.key}>
            <label className="block text-sm font-medium text-neutral-600 mb-1">
              {f.label}
            </label>
            <textarea
              value={displayValue}
              onChange={(e) => handleChange(f.key, f.type, e.target.value)}
              className="w-full min-h-[80px] p-3 border border-neutral-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 text-sm"
              placeholder={
                f.type === "list"
                  ? `Enter ${f.label.toLowerCase()} (one per line)...`
                  : `Enter ${f.label.toLowerCase()}...`
              }
            />
          </div>
        )
      })}
    </div>
  )
}

/**
 * Convert structured sub-field data to a narrative string matching
 * the backend's `to_narrative()` format: **Label:** content pattern.
 */
function formatField(label: string, value: SOAPSentence | undefined | null): string | null {
  if (!value || !value.text.trim()) return null
  return `**${label}:** ${value.text.trim()}`
}

function formatListField(label: string, items: SOAPSentence[] | null | undefined): string | null {
  if (!items) return null
  const nonEmpty = items.map((s) => s.text.trim()).filter(Boolean)
  if (nonEmpty.length === 0) return null
  const bullets = nonEmpty.map((item) => `- ${item}`).join("\n")
  return `**${label}:**\n${bullets}`
}

function joinParts(parts: (string | null)[]): string {
  return parts.filter(Boolean).join("\n\n")
}

export interface StructuredEditState {
  subjective: SubjectiveNote
  objective: ObjectiveNote
  assessment: AssessmentNote
  plan: PlanNote
}

export function structuredToNarrative(state: StructuredEditState) {
  const s = state.subjective
  const o = state.objective
  const a = state.assessment
  const p = state.plan

  return {
    subjective: joinParts([
      formatField("Chief Complaint", s.chief_complaint),
      formatField("Mood/Affect", s.mood_affect),
      formatListField("Symptoms", s.symptoms),
      formatField("Client Narrative", s.client_narrative),
    ]),
    objective: joinParts([
      formatField("Appearance", o.appearance),
      formatField("Behavior", o.behavior),
      formatField("Speech", o.speech),
      formatField("Thought Process", o.thought_process),
      formatField("Affect Observed", o.affect_observed),
    ]),
    assessment: joinParts([
      formatField("Clinical Impression", a.clinical_impression),
      formatField("Progress", a.progress),
      formatField("Risk Assessment", a.risk_assessment),
      formatField("Functioning Level", a.functioning_level),
    ]),
    plan: joinParts([
      formatListField("Interventions Used", p.interventions_used),
      formatListField("Homework Assignments", p.homework_assignments),
      formatListField("Next Steps", p.next_steps),
      formatField("Next Session", p.next_session),
    ]),
  }
}

/**
 * Parse narrative text back to structured sub-fields for initial edit state.
 * Reverses the **Label:** content pattern. Falls back to catch-all field.
 */
export function narrativeToStructured(narrative: {
  subjective: string
  objective: string
  assessment: string
  plan: string
}): StructuredEditState {
  return {
    subjective: parseSubjective(narrative.subjective),
    objective: parseObjective(narrative.objective),
    assessment: parseAssessment(narrative.assessment),
    plan: parsePlan(narrative.plan),
  }
}

function parseFields(text: string): Record<string, string> {
  const result: Record<string, string> = {}
  if (!text.trim()) return result

  // Split on **Label:** patterns
  const parts = text.split(/\*\*([^*]+):\*\*\s*/)
  if (parts.length <= 1) {
    result["_fallback"] = text.trim()
    return result
  }

  for (let i = 1; i < parts.length; i += 2) {
    const label = parts[i]
    const content = (parts[i + 1] ?? "").trim()
    if (label) result[label] = content
  }
  return result
}

function parseBullets(text: string): SOAPSentence[] | null {
  if (!text) return null
  const items = text.split("\n")
    .map((line) => line.replace(/^-\s*/, "").trim())
    .filter(Boolean)
  return items.length > 0 ? sentenceList(items) : null
}

function parseSubjective(text: string): SubjectiveNote {
  const fields = parseFields(text)
  return {
    chief_complaint: sentence(fields["Chief Complaint"] ?? ""),
    mood_affect: sentence(fields["Mood/Affect"] ?? ""),
    symptoms: parseBullets(fields["Symptoms"] ?? ""),
    client_narrative: sentence(fields["Client Narrative"] ?? fields["_fallback"] ?? ""),
  }
}

function parseObjective(text: string): ObjectiveNote {
  const fields = parseFields(text)
  return {
    appearance: sentence(fields["Appearance"] ?? ""),
    behavior: sentence(fields["Behavior"] ?? fields["_fallback"] ?? ""),
    speech: sentence(fields["Speech"] ?? ""),
    thought_process: sentence(fields["Thought Process"] ?? ""),
    affect_observed: sentence(fields["Affect Observed"] ?? ""),
  }
}

function parseAssessment(text: string): AssessmentNote {
  const fields = parseFields(text)
  return {
    clinical_impression: sentence(fields["Clinical Impression"] ?? fields["_fallback"] ?? ""),
    progress: sentence(fields["Progress"] ?? ""),
    risk_assessment: sentence(fields["Risk Assessment"] ?? ""),
    functioning_level: sentence(fields["Functioning Level"] ?? ""),
  }
}

function parsePlan(text: string): PlanNote {
  const fields = parseFields(text)
  return {
    interventions_used: parseBullets(fields["Interventions Used"] ?? ""),
    homework_assignments: parseBullets(fields["Homework Assignments"] ?? ""),
    next_steps: parseBullets(fields["Next Steps"] ?? ""),
    next_session: sentence(fields["Next Session"] ?? fields["_fallback"] ?? ""),
  }
}
