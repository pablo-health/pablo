// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Coverage on file: the practice's payer list and a client's plan.
 *
 * Mirrors `backend/app/models/coverage.py`. Field names are snake_case to
 * match the API exactly.
 */

export type SubscriberRelationship = "self" | "spouse" | "child" | "other"
export type EnrollmentStatus = "none" | "filed" | "pending" | "active" | "error"
export type EnrollmentTransactionType = "837P" | "270" | "835"
export type EnrollmentRequestStatus =
  | "draft"
  | "stedi_action_required"
  | "provider_action_required"
  | "provisioning"
  | "live"
  | "rejected"
  | "canceled"
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

/** One enrollment request with the payer, per transaction type. */
export interface PayerEnrollmentResponse {
  transaction_type: EnrollmentTransactionType
  vendor_request_id: string
  status: EnrollmentRequestStatus
  /** The clearinghouse's wording of what the payer needs; null unless it is waiting on the practice. */
  instructions: string | null
  updated_at: string
}

export interface PayerEnrollmentListResponse {
  data: PayerEnrollmentResponse[]
  enrollment_status: EnrollmentStatus
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

export interface CoverageResponse extends SubscriberFields {
  id: string
  patient_id: string
  payer: PayerResponse
  member_id: string
  group_number: string | null
  plan_name: string | null
  active: boolean
  /** When an eligibility check last confirmed the plan; null until one has run. */
  verified_at: string | null
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
