// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The unbilled-sessions queue.
 *
 * Field names are snake_case to match `app.models.billing_queue` exactly.
 */

/** One finalized session with no succeeded charge. */
export interface UnbilledSessionItem {
  session_id: string
  patient_id: string
  patient_name: string
  session_date: string
  /** `null` when neither the client nor an appointment type sets a rate. */
  amount_cents: number | null
  currency: string
}

export interface UnbilledQueueResponse {
  items: UnbilledSessionItem[]
}
