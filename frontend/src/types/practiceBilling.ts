// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's billing identity and its billing-side switches.
 *
 * Mirrors `backend/app/models/practice_billing.py`. The tax id never comes
 * back in full: only `tax_id_last4` is ever read.
 */

export interface BillingProfileResponse {
  legal_name: string | null
  tax_id_last4: string | null
  tax_id_type: "ein" | "ssn" | null
  billing_npi: string | null
  address_line1: string | null
  address_line2: string | null
  city: string | null
  state: string | null
  postal_code: string | null
  phone: string | null
  /** Run an eligibility check on its own whenever coverage lands. */
  eligibility_auto_check: boolean
}

export interface UpdateBillingProfileRequest {
  legal_name?: string
  /** Write-only: accepted here, never echoed back. */
  tax_id?: string
  tax_id_type?: "ein" | "ssn"
  billing_npi?: string
  address_line1?: string
  address_line2?: string
  city?: string
  state?: string
  postal_code?: string
  phone?: string
  eligibility_auto_check?: boolean
}
