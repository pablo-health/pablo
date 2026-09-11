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

/**
 * Where a rejection finding came from: a clearinghouse edit on the
 * synchronous answer, or a claim status from the clearinghouse or the payer.
 */
export type SubmissionFindingSource = "edit" | "status"

/**
 * One thing the clearinghouse or the payer found wrong with a filed claim.
 * `description` is the vendor's own wording and can quote the field at fault,
 * so it belongs in the audited claim detail view and nowhere else.
 */
export interface SubmissionFinding {
  source: SubmissionFindingSource
  code: string
  description: string
  followup_action: string | null
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
  /** The clearinghouse's id for the filing, once it has taken one. */
  vendor_claim_id: string | null
  /** The payer's own number for the claim, once its status has named one. */
  payer_claim_number: string | null
  /** The outbox's pending marker: set while a filing attempt is in flight. */
  submission_pending_at: string | null
  submission_findings: SubmissionFinding[]
  last_receipt_at: string | null
  status_checked_at: string | null
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

/**
 * One hop the claim took, or one alert raised about it. A receipt whose
 * `to_state` differs from its `from_state` moved the claim; one where they
 * match moved nothing and is a note on the timeline.
 */
export type ClaimReceiptKind =
  | "submitted"
  | "ch_accepted"
  | "payer_accepted"
  | "rejected"
  | "stalled"
  | "acknowledged"
  | "status_checked"
  | "deadline_approaching"
  | "deadline_missed"

/** `detail` carries codes and vendor identifiers only — never a name or a member id. */
export interface ClaimReceipt {
  id: string
  claim_id: string
  kind: ClaimReceiptKind
  from_state: ClaimState | null
  to_state: ClaimState | null
  deadline_kind: DeadlineKind | null
  rung: number | null
  vendor_event_id: string | null
  vendor_transaction_id: string | null
  detail: Record<string, unknown>
  occurred_at: string
}

/** What a person does next with the claim; null on a paid one, which needs nothing. */
export type NextAction =
  | "review_and_file"
  | "queued_to_send"
  | "sending"
  | "await_acknowledgment"
  | "await_payer"
  | "await_remittance"
  | "review_remittance"
  | "correct_and_resubmit"
  | "appeal_or_correct"
  | "check_with_clearinghouse"

export interface ClaimDetailResponse extends ClaimResponse {
  patient_name: string
  payer_name: string | null
  findings: ClaimFinding[]
  hops: ClaimHop[]
  deadlines: ClaimDeadlines
  receipts: ClaimReceipt[]
  next_action: NextAction | null
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
  last_receipt_at: string | null
  created_at: string
  updated_at: string
  deadlines: ClaimDeadlines
  next_action: NextAction | null
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

/**
 * A remittance whose own numbers disagreed, so the client was not billed.
 *
 * `statedCents` is what the payer asserted; `computedCents` is the same
 * figure worked out from the rest of the document. They are kept apart on
 * purpose — a therapist deciding whether to bill needs to see which side is
 * which, not a single delta.
 */
export type RemittanceHoldReason = "patient_responsibility" | "line_balance" | "claim_balance"

export type RemittanceHoldState = "open" | "acknowledged" | "resolved"

export type RemittanceHoldFinding =
  | "bill_as_stated"
  | "waived"
  | "parse_error"
  | "payer_inconsistent"

export interface RemittanceAdjustmentCode {
  group_code: string
  reason_code: string
}

export interface RemittanceHold {
  id: string
  claim_id: string
  control_number: string
  state: RemittanceHoldState
  reason: RemittanceHoldReason
  stated_cents: number
  computed_cents: number
  delta_cents: number
  patient_responsibility_cents: number
  line_control_number: string | null
  codes: RemittanceAdjustmentCode[]
  line_count: number
  payer_name: string | null
  detected_at: string
  acknowledged_at: string | null
  resolved_at: string | null
  finding: RemittanceHoldFinding | null
}

export interface RemittanceHoldListResponse {
  data: RemittanceHold[]
  total: number
}

/**
 * A remittance whose own numbers disagreed, so the client was not billed.
 *
 * `statedCents` is what the payer asserted; `computedCents` is the same
 * figure worked out from the rest of the document. They are kept apart on
 * purpose — a therapist deciding whether to bill needs to see which side is
 * which, not a single delta.
 */
export type RemittanceHoldReason = "patient_responsibility" | "line_balance" | "claim_balance"

export type RemittanceHoldState = "open" | "acknowledged" | "resolved"

export type RemittanceHoldFinding =
  | "bill_as_stated"
  | "waived"
  | "parse_error"
  | "payer_inconsistent"

export interface RemittanceAdjustmentCode {
  group_code: string
  reason_code: string
}

export interface RemittanceHold {
  id: string
  claim_id: string
  control_number: string
  state: RemittanceHoldState
  reason: RemittanceHoldReason
  stated_cents: number
  computed_cents: number
  delta_cents: number
  patient_responsibility_cents: number
  line_control_number: string | null
  codes: RemittanceAdjustmentCode[]
  line_count: number
  payer_name: string | null
  detected_at: string
  acknowledged_at: string | null
  resolved_at: string | null
  finding: RemittanceHoldFinding | null
}

export interface RemittanceHoldListResponse {
  data: RemittanceHold[]
  total: number
}

/**
 * A remittance whose own numbers disagreed, so the client was not billed.
 *
 * `statedCents` is what the payer asserted; `computedCents` is the same
 * figure worked out from the rest of the document. They are kept apart on
 * purpose — a therapist deciding whether to bill needs to see which side is
 * which, not a single delta.
 */
export type RemittanceHoldReason = "patient_responsibility" | "line_balance" | "claim_balance"

export type RemittanceHoldState = "open" | "acknowledged" | "resolved"

export type RemittanceHoldFinding =
  | "bill_as_stated"
  | "waived"
  | "parse_error"
  | "payer_inconsistent"

export interface RemittanceAdjustmentCode {
  group_code: string
  reason_code: string
}

export interface RemittanceHold {
  id: string
  claim_id: string
  control_number: string
  state: RemittanceHoldState
  reason: RemittanceHoldReason
  stated_cents: number
  computed_cents: number
  delta_cents: number
  patient_responsibility_cents: number
  line_control_number: string | null
  codes: RemittanceAdjustmentCode[]
  line_count: number
  payer_name: string | null
  detected_at: string
  acknowledged_at: string | null
  resolved_at: string | null
  finding: RemittanceHoldFinding | null
}

export interface RemittanceHoldListResponse {
  data: RemittanceHold[]
  total: number
}
