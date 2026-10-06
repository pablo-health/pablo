// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Problem list API types
 *
 * Mirrors backend `app.problems.schemas`. The problem list is the record of a
 * client's diagnoses: each entry has a label, an optional ICD-10-CM code and a
 * status. The patient's `diagnosis` line is derived from the active entries.
 */

export type ProblemStatus = "active" | "rule_out" | "resolved"

export interface Problem {
  id: string
  patient_id: string
  label: string
  icd10_code: string | null
  status: ProblemStatus
  onset_date: string | null
  position: number
  source_note_id: string | null
  added_by: string | null
  added_at: string
  resolved_at: string | null
  updated_at: string
}

export interface ProblemListResponse {
  data: Problem[]
  total: number
}

export interface AddProblemRequest {
  label: string
  icd10_code?: string | null
  status?: ProblemStatus
  onset_date?: string | null
  /** The note a problem is added from, when it is. */
  source_note_id?: string | null
  diagnostic_assessment_id?: string | null
}

export interface UpdateProblemRequest {
  label?: string
  icd10_code?: string | null
  status?: ProblemStatus
  onset_date?: string | null
}

/** The shape of an ICD-10-CM code; mirrors the backend check. */
export const ICD10_CODE_PATTERN = /^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$/
