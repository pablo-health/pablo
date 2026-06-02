// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Outcome measure API types
 *
 * Mirrors backend `app.outcome_measures.schemas`. An outcome measure is a
 * single scored administration of a standardized clinical instrument
 * (PHQ-9, GAD-7, ...) for a patient. The backend owns scoring: it derives
 * `total_score`, `is_complete`, and `severity` from the submitted
 * `item_scores`. The UI posts item responses and renders what comes back.
 */

/** Clinical provenance of a score. Mirrors backend `OutcomeMeasureSource`. */
export type OutcomeMeasureSource =
  | "patient_self_report"
  | "clinician_administered_verbal"
  | "manual"
  | "inferred"

/**
 * A single scored instrument administration. Mirrors `OutcomeMeasureResponse`
 * from the backend. `total_score` and `severity` are null until the
 * administration is complete (all items present).
 */
export interface OutcomeMeasure {
  id: string
  patient_id: string
  session_id: string | null
  appointment_id: string | null
  instrument: string
  total_score: number | null
  item_scores: Record<string, number> | null
  is_complete: boolean
  source: string
  item_citations: Record<string, unknown> | null
  administered_at: string
  created_by: string
  created_at: string
  updated_at: string
  /** Severity label computed server-side from the total; null until complete. */
  severity: string | null
}

export interface OutcomeMeasureListResponse {
  data: OutcomeMeasure[]
  total: number
}

/**
 * Body for `POST /api/patients/{patient_id}/outcome-measures`. At least one
 * of `item_scores` or `total_score` must be provided; the manual-entry form
 * always sends `source: "manual"`.
 */
export interface CreateOutcomeMeasureRequest {
  instrument: string
  source: OutcomeMeasureSource
  administered_at: string
  session_id?: string | null
  appointment_id?: string | null
  item_scores?: Record<string, number> | null
  total_score?: number | null
}
