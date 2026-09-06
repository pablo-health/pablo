// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Coverage on file: the practice's payer list and a client's plan.
 *
 * Mirrors `backend/app/models/coverage.py`. Field names are snake_case to
 * match the API exactly.
 */

export type SubscriberRelationship = "self" | "spouse" | "child" | "other"
export type EnrollmentStatus = "none" | "filed" | "pending" | "active" | "error"
export type AdministrativeSex = "M" | "F" | "U"

export interface PayerResponse {
  id: string
  name: string
  /** The electronic payer id from the card or the payer directory. */
  payer_id: string
  clearinghouse_payer_id: string | null
  is_carveout: boolean
  carveout_of: string | null
  enrollment_status: EnrollmentStatus
  /** Days after the service an original claim may be filed. */
  timely_filing_days: number
  /** Days after a rejection a corrected claim may follow. */
  corrected_claim_days: number
  /** Days after a denial an appeal may be lodged. */
  appeal_days: number
  created_at: string
  updated_at: string
}

export interface PayerListResponse {
  data: PayerResponse[]
  total: number
}

export interface CreatePayerRequest {
  name: string
  payer_id: string
  is_carveout?: boolean
  carveout_of?: string | null
  /** Omitted, the server picks the default for the payer id. */
  timely_filing_days?: number
  corrected_claim_days?: number
  appeal_days?: number
}

export interface UpdatePayerRequest {
  name?: string
  payer_id?: string
  is_carveout?: boolean
  carveout_of?: string | null
  timely_filing_days?: number
  corrected_claim_days?: number
  appeal_days?: number
}

export interface SubscriberFields {
  subscriber_relationship: SubscriberRelationship
  subscriber_first_name: string | null
  subscriber_last_name: string | null
  /** ISO date (YYYY-MM-DD). */
  subscriber_date_of_birth: string | null
  subscriber_sex: AdministrativeSex | null
  subscriber_address_line1: string | null
  subscriber_address_line2: string | null
  subscriber_city: string | null
  subscriber_state: string | null
  subscriber_postal_code: string | null
}

/**
 * What the last eligibility check found. Mirrors
 * `backend/app/models/eligibility.py`.
 *
 * `active` and `inactive` are the payer's answer; `unknown` is a 271 that
 * answered without saying either way for this benefit; `error` is a payer
 * refusal (an AAA rejection) — the payer never answered the coverage
 * question. None of it is a payment guarantee.
 */
export type EligibilityStatus = "active" | "inactive" | "unknown" | "error"

/** Somebody other than the payer on the card administers behavioral benefits. */
export interface CarveoutAdministrator {
  name: string
  /** The administrator's electronic payer id, when the 271 carried one. */
  payer_id: string | null
}

export interface VisitLimit {
  remaining: number | null
  total: number | null
}

export interface AaaError {
  code: string
  description: string
  followup_action: string
  /** The vendor's plain-language "what to do about it". */
  resolution: string | null
}

export interface EligibilitySummary {
  status: EligibilityStatus
  checked_at: string
  payer_name: string | null
  plan_name: string | null
  /** ISO date (YYYY-MM-DD). */
  plan_begin: string | null
  copay_cents: number | null
  coinsurance_pct: number | null
  deductible_remaining_cents: number | null
  visit_limit: VisitLimit | null
  requires_authorization: boolean | null
  carveout_administrator: CarveoutAdministrator | null
  aaa_errors: AaaError[]
}

export interface CoverageResponse extends SubscriberFields {
  id: string
  patient_id: string
  payer: PayerResponse
  member_id: string
  group_number: string | null
  plan_name: string | null
  active: boolean
  /** When an eligibility check last asked the payer; null until one has run. */
  verified_at: string | null
  /** The stored 271 read down; null until a check has run. */
  eligibility: EligibilitySummary | null
  created_at: string
  updated_at: string
}

/** The payer picker's free-text fallback: a payer typed from the card. */
export interface NewPayerInline {
  name: string
  payer_id: string
}

export interface CreateCoverageRequest extends Partial<SubscriberFields> {
  /** A payer already on the list. Exactly one of this or `new_payer`. */
  payer_id?: string
  new_payer?: NewPayerInline
  member_id: string
  group_number?: string | null
  plan_name?: string | null
}

export interface UpdateCoverageRequest extends Partial<SubscriberFields> {
  payer_id?: string
  member_id?: string
  group_number?: string | null
  plan_name?: string | null
}
