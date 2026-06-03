// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Diagnostic-criteria engine API types
 *
 * Mirrors backend `app.diagnostics.schemas`. A diagnostic assessment is a
 * structured determination for a patient: the clinician records which
 * criteria are met (per-criterion + duration/impairment/exclusion gates), the
 * engine computes whether the rubric is satisfied, and the clinician confirms
 * an ICD-10-CM code. Definitions are data — the form is rendered from
 * `GET /api/diagnostic-definitions`, never hard-coded per disorder.
 */

import type { OutcomeMeasureSource } from "@/types/outcomeMeasures"

/** Clinical provenance of a determination. The backend shares the
 * `OutcomeMeasureSource` enum; the manual form always sends `"manual"`. */
export type DiagnosticSource = OutcomeMeasureSource

// --- Definition (the rubric, for rendering the form) ---------------------

export interface CriterionView {
  key: string
  label: string
  /** A "core" symptom — when its group requires a cardinal, at least one met
   * criterion must be cardinal. */
  cardinal: boolean
}

export interface CriterionGroupView {
  key: string
  label: string
  /** Minimum criteria in this group that must be met. */
  min_met: number
  require_cardinal: boolean
  criteria: CriterionView[]
}

export interface GateView {
  key: string
  label: string
}

export interface ICD10OptionView {
  code: string
  label: string
}

export interface DiagnosticDefinition {
  code: string
  version: number
  display_name: string
  evaluator_type: string
  criterion_groups: CriterionGroupView[]
  gates: GateView[]
  icd10_options: ICD10OptionView[]
  /** The code suggested when criteria are met — only a suggestion; the
   * clinician confirms or picks a specifier. */
  suggested_icd10: string | null
}

export interface DiagnosticDefinitionListResponse {
  data: DiagnosticDefinition[]
  total: number
}

// --- Assessment (a recorded determination) -------------------------------

export interface DiagnosticAssessment {
  id: string
  patient_id: string
  session_id: string | null
  appointment_id: string | null
  instrument: string
  definition_version: number
  criterion_responses: Record<string, boolean>
  gate_responses: Record<string, boolean>
  /** `null` for checklist definitions, which make no algorithmic determination. */
  meets_criteria: boolean | null
  determined_icd10: string | null
  diagnosis_label: string | null
  source: string
  confirmed_at: string | null
  assessed_at: string
  created_by: string
  created_at: string
  updated_at: string
  /** Recomputed at read time from the snapshotted definition; not stored. */
  suggested_icd10: string | null
  unmet_reasons: string[]
}

export interface DiagnosticAssessmentListResponse {
  data: DiagnosticAssessment[]
  total: number
}

/**
 * Body for `POST /api/patients/{patient_id}/diagnostic-assessments`.
 * `criterion_responses` / `gate_responses` are keyed by the definition's
 * criterion / gate keys; `determined_icd10`, when supplied, must be one of the
 * definition's `icd10_options`.
 */
export interface CreateDiagnosticAssessmentRequest {
  instrument: string
  source: DiagnosticSource
  assessed_at: string
  criterion_responses: Record<string, boolean>
  gate_responses: Record<string, boolean>
  session_id?: string | null
  appointment_id?: string | null
  determined_icd10?: string | null
  diagnosis_label?: string | null
}
