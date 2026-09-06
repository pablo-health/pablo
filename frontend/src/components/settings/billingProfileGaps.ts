// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a claim, and the clearinghouse's provider record, still need from
 * the practice profile.
 *
 * Mirrors the scrub's required billing-provider fields
 * (`backend/app/claims/scrub.py`) and the provider-record fields
 * (`backend/app/claims/enrollment.py`), so the banner on the settings page
 * names the same gaps a claim review would refuse on.
 */

import type { BillingProfileResponse } from "@/types/practiceBilling"

export interface ClinicianIdentifiers {
  npi_number: string | null
  taxonomy_code: string | null
}

export interface BillingProfileGaps {
  /** Blank fields a claim is refused without, in the order the form shows them. */
  claims: string[]
  /** Blank fields the clearinghouse also needs before the practice can be registered. */
  clearinghouse: string[]
  /** Blank fields a claim goes out without, but some payers ask for. */
  advisable: string[]
}

const ADDRESS_FIELDS = ["address_line1", "city", "state", "postal_code"] as const

export function billingProfileGaps(
  profile: BillingProfileResponse,
  clinician: ClinicianIdentifiers,
): BillingProfileGaps {
  const claims: string[] = []
  if (!profile.legal_name) claims.push("legal name")
  if (!profile.tax_id_last4) claims.push("tax id")
  if (!profile.tax_id_type) claims.push("tax id type")
  if (ADDRESS_FIELDS.some((field) => !profile[field])) claims.push("billing address")
  if (!profile.phone) claims.push("phone")
  if (!clinician.npi_number) claims.push("your NPI")

  const clearinghouse: string[] = []
  if (!profile.billing_npi) clearinghouse.push("billing NPI")
  if (!profile.contact_email) clearinghouse.push("contact email")

  const advisable: string[] = []
  if (!clinician.taxonomy_code) advisable.push("taxonomy code")

  return { claims, clearinghouse, advisable }
}
