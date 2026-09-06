// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Claims: a claim built from a session, the tracker that lists them, and the
 * biller export's refusal shape.
 *
 * Field names are snake_case to match `app.models.claims` exactly.
 */

/**
 * Where a claim stands. It only ever moves forward on a receipt from the
 * next hop; `rejected` and `stalled` are the two side exits.
 */
export type ClaimState =
  | "draft"
  | "validated"
  | "submitted"
  | "ch_accepted"
  | "payer_accepted"
  | "paid"
  | "partial"
  | "denied"
  | "rejected"
  | "stalled"

export const CLAIM_STATES: readonly ClaimState[] = [
  "draft",
  "validated",
  "submitted",
  "ch_accepted",
  "payer_accepted",
  "paid",
  "partial",
  "denied",
  "rejected",
  "stalled",
]

/** `1` an original claim, `7` a replacement, `8` a void. */
export type FrequencyCode = "1" | "7" | "8"

export type DeadlineKind = "filing" | "correction" | "appeal"

export interface ClaimFinding {
  severity: "blocking" | "warning"
  code: string
  message: string
  field: string | null
}

export interface ClaimLine {
  id: string
  claim_id: string
  patient_id: string
  appointment_id: string | null
  line_number: number
  line_control_number: string
  /** ISO date (YYYY-MM-DD). */
  service_date: string
  cpt: string
  modifiers: string[]
  units: number
  charge_cents: number
  dx_pointers: number[]
  telehealth: boolean
  allowed_cents: number | null
  paid_cents: number
  patient_resp_cents: number | null
  adjustments: Record<string, unknown>[] | null
  created_at: string
}

export interface ClaimResponse {
  id: string
  control_number: string
  patient_id: string
  coverage_id: string
  payer_id: string
  state: ClaimState
  frequency_code: FrequencyCode
  parent_claim_id: string | null
  total_charge_cents: number
  total_paid_cents: number
  diagnosis_codes: string[]
  place_of_service: string | null
  submitted_at: string | null
  payer_accepted_at: string | null
  adjudicated_at: string | null
  created_at: string
  updated_at: string
  lines: ClaimLine[]
}

/**
 * The claim's clocks. `applicable` is the one that binds right now and
 * `days_left` counts down to it, going negative once it has passed; both are
 * null for a claim under no clock (paid, or a void).
 */
export interface ClaimDeadlines {
  filing: string | null
  correction: string | null
  appeal: string | null
  applicable: DeadlineKind | null
  days_left: number | null
}

export type ClaimHopKind =
  | "built"
  | "submitted"
  | "clearinghouse_accepted"
  | "payer_accepted"
  | "adjudicated"

export interface ClaimHop {
  kind: ClaimHopKind
  reached: boolean
  at: string | null
}

export interface ClaimDetailResponse extends ClaimResponse {
  patient_name: string
  payer_name: string | null
  findings: ClaimFinding[]
  hops: ClaimHop[]
  deadlines: ClaimDeadlines
}

/** One row of the tracker: no snapshots, just what the table shows. */
export interface ClaimTrackerItem {
  id: string
  control_number: string
  patient_id: string
  patient_name: string
  payer_id: string
  payer_name: string | null
  state: ClaimState
  frequency_code: FrequencyCode
  parent_claim_id: string | null
  service_date: string | null
  total_charge_cents: number
  total_paid_cents: number
  submitted_at: string | null
  created_at: string
  updated_at: string
  deadlines: ClaimDeadlines
}

export interface ClaimTrackerResponse {
  data: ClaimTrackerItem[]
  total: number
}

export interface ClaimTrackerFilters {
  state?: ClaimState
  /** ISO calendar dates (`YYYY-MM-DD`), both ends inclusive. */
  from?: string
  to?: string
}

export interface ValidateClaimResponse {
  claim: ClaimResponse
  findings: ClaimFinding[]
}

export interface AddOnService {
  cpt: string
  charge_cents: number
}

export interface BuildClaimRequest {
  add_on?: AddOnService | null
}

/** The 422 from `/validate`: the claim stays a draft. */
export const CLAIM_VALIDATION_FAILED = "CLAIM_VALIDATION_FAILED"

export interface ClaimExportFinding {
  claim_id: string
  control_number: string
  findings: ClaimFinding[]
}

export const CLAIM_EXPORT_BLOCKED = "CLAIM_EXPORT_BLOCKED"
