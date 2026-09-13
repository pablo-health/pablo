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
  /**
   * What the practice wants Pablo enrolled for with this payer. These gate
   * enrollment, not traffic — a transaction the payer needs no enrollment for
   * works either way. `enroll_remittance` is the consequential one: completing
   * that enrollment moves the payer's ERAs here from wherever they arrive now.
   */
  enroll_eligibility: boolean
  enroll_claims: boolean
  enroll_remittance: boolean
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

/** What a task wants: text the practice types, or a PDF it uploads. */
export type EnrollmentFieldType = "TEXT" | "DOCUMENT"

export interface EnrollmentTaskField {
  /** The clearinghouse's own key for the field; what an answer is filed under. */
  key: string
  label: string
  field_type: EnrollmentFieldType
  description: string | null
}

export interface EnrollmentTaskLink {
  label: string
  url: string
  /**
   * True when the link points back at the clearinghouse's own API, which
   * answers a browser with 403. Open those through
   * `resolveEnrollmentTaskLink`; follow any other link directly.
   */
  resolvable: boolean
}

/**
 * One thing the payer is waiting on, shaped as the form to fill in.
 *
 * `fields` empty means the task is the instructions themselves — done in a
 * payer's portal or over the telephone — and answering it says the practice
 * did that.
 */
export interface EnrollmentTaskResponse {
  id: string
  instructions: string | null
  links: EnrollmentTaskLink[]
  fields: EnrollmentTaskField[]
}

export type EnrollmentDocumentStatus = "PENDING" | "UPLOADED" | "FAILED"

export interface EnrollmentDocumentResponse {
  id: string
  name: string | null
  status: EnrollmentDocumentStatus
}

export interface EnrollmentTaskListResponse {
  data: EnrollmentTaskResponse[]
  status: EnrollmentRequestStatus
  /** Every PDF on the enrollment, whichever side put it there. */
  documents: EnrollmentDocumentResponse[]
}

export interface EnrollmentDocumentUrlResponse {
  url: string
}

/** What a practice-wide refresh pass answered. */
export interface PayerEnrollmentRefreshResponse {
  changed: number
  checked_at: string
  /** True when this is the previous pass's answer, inside the throttle floor. */
  throttled: boolean
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
  enroll_eligibility?: boolean
  enroll_claims?: boolean
  enroll_remittance?: boolean
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
  /**
   * What the practice collects at the door, when it knows better than the
   * payer's answer. `null` is "no override", not "no copay".
   */
  copay_override_cents: number | null
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
  copay_override_cents?: number | null
}

export interface UpdateCoverageRequest extends Partial<SubscriberFields> {
  payer_id?: string
  member_id?: string
  group_number?: string | null
  plan_name?: string | null
  /** Send `null` to drop the override and fall back to the payer's answer. */
  copay_override_cents?: number | null
}

/**
 * One payer the clearinghouse directory knows about.
 *
 * `requires_enrollment` is why the picker exists rather than a bare name
 * search: the directory says, per transaction, whether an enrollment has to be
 * filed before the practice can use it. Told at pick time that is information;
 * told afterwards it is a surprise.
 */
export interface PayerDirectoryMatch {
  payer_id: string
  name: string
  aliases: string[]
  /** Transaction types needing enrollment: "837P", "270", "835". */
  requires_enrollment: string[]
  /** Already on the practice's list, so the row offers no duplicate. */
  already_added: boolean
}

export interface PayerDirectoryResult {
  matches: PayerDirectoryMatch[]
  /** The clearinghouse could not be asked — not the same as "no such payer". */
  unavailable: boolean
}
