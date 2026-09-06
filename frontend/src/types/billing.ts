// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The unbilled-sessions queue.
 *
 * Field names are snake_case to match `app.models.billing_queue` exactly.
 */

import type { ClaimState, FrequencyCode } from "./claims"

/** The newest claim already filed for the row's visit. */
export interface UnbilledClaimSummary {
  id: string
  control_number: string
  state: ClaimState
  frequency_code: FrequencyCode
}

/** One finalized session with no succeeded charge. */
export interface UnbilledSessionItem {
  session_id: string
  patient_id: string
  patient_name: string
  session_date: string
  /** `null` when neither the client nor an appointment type sets a rate. */
  amount_cents: number | null
  currency: string
  /** `null` for a session that was never booked as an appointment; a claim needs one. */
  appointment_id: string | null
  /** The client has active coverage on file, so a claim can be filed. */
  has_coverage: boolean
  /** The newest claim on the visit; `null` when none has been filed. */
  claim: UnbilledClaimSummary | null
}

export interface UnbilledQueueResponse {
  items: UnbilledSessionItem[]
}
