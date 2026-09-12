// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Credentialing checklist types.
 *
 * Mirrors backend/app/routes/credentialing.py response shapes. The question
 * set itself is NOT declared here — it arrives from the server, because the
 * branching the API enforces and the branching the screen shows have to be the
 * same branching. A copy of the field list in the client is a copy that
 * eventually disagrees about what is required of whom.
 */

export type ChecklistTier =
  | "tier_0_confirm"
  | "tier_1_claims_ready"
  | "tier_2_credentialing"

export type ChecklistFieldKind =
  | "text"
  | "date"
  | "boolean"
  | "choice"
  | "money"
  | "collection"
  | "upload"

export type ChecklistApplicability =
  | "all"
  | "independent_only"
  | "supervised_only"
  | "prescriber_only"

/** Where a Tier-0 value came from, so the surface can say why it knows. */
export type ConfirmationSource =
  | "nppes"
  | "pecos_public_file"
  | "leie_sam"
  | "clinician_profiles"
  | "practice_billing_profile"

export interface ChecklistField {
  key: string
  label: string
  section: string
  tier: ChecklistTier
  kind: ChecklistFieldKind
  required: boolean
  applies_to: ChecklistApplicability
  source: ConfirmationSource | null
  help_text: string | null
  choices: string[]
  answered: boolean
  /**
   * What the record holds for this field right now. Tier 0 only — the tier
   * that asks her to agree with a value rather than to type one. `null` means
   * nothing is on file, which the card says rather than showing a blank.
   */
  current_value: string | null
}

export interface TierProgress {
  tier: ChecklistTier
  answered: number
  required: number
  complete: boolean
}

export interface ChecklistSurface {
  supervised: boolean
  prescriber: boolean
  /** Every question the billing pipeline needs has an answer. */
  claims_ready: boolean
  progress: TierProgress[]
  fields: ChecklistField[]
}

export interface Confirmation {
  field_key: string
  source: ConfirmationSource
  presented_value: string | null
  confirmed: boolean
  correction: string | null
  confirmed_at: string
}

export interface ConfirmationPayload {
  source: ConfirmationSource
  confirmed: boolean
  presented_value?: string | null
  /** Required when `confirmed` is false. */
  correction?: string | null
}

export interface ChecklistAnswers {
  supervision_status?: string | null
  caqh_id?: string | null
  business_structure?: string | null
  sole_proprietor?: boolean | null
  type2_npi?: string | null
  medicare_intent?: boolean | null
  medicaid_intent?: boolean | null
}
