// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Grouping for the reminder composer's browse view. Mirrors how a solo
 * therapist mentally files these obligations — credentials vs. payer
 * paperwork vs. security ops — so 21 templates feel like five short lists
 * instead of one long one.
 */

export type CategoryId =
  | "credentials"
  | "payer"
  | "training"
  | "vendors"
  | "operations"
  | "other"

export interface CategoryDef {
  id: CategoryId
  label: string
  hint: string
}

export const CATEGORIES: CategoryDef[] = [
  {
    id: "credentials",
    label: "Credentials & licensure",
    hint: "Licenses, NPI, CEUs",
  },
  {
    id: "payer",
    label: "Payer & insurance",
    hint: "CAQH, liability, enrollment",
  },
  {
    id: "training",
    label: "HIPAA, training & audits",
    hint: "Annual refreshers and assessments",
  },
  { id: "vendors", label: "Vendors & BAAs", hint: "Business Associate Agreements" },
  {
    id: "operations",
    label: "Security operations",
    hint: "Backups, scans, audits",
  },
  { id: "other", label: "Other", hint: "Custom reminders" },
]

const TYPE_TO_CATEGORY: Record<string, CategoryId> = {
  license: "credentials",
  npi: "credentials",
  ceu_credits: "credentials",
  telehealth_licensure: "credentials",
  mandated_reporter_training: "credentials",
  // Prescriber credentials
  dea_registration: "credentials",
  board_certification: "credentials",
  dea_mate_training: "credentials",
  supervision_review: "credentials",

  liability_insurance: "payer",
  caqh_attestation: "payer",
  payer_enrollment: "payer",

  hipaa_training: "training",
  security_risk_assessment: "training",
  compliance_audit: "training",

  baa: "vendors",
  vendor_inventory: "vendors",
  vendor_verification: "vendors",

  audit_log_review: "operations",
  backup_verification: "operations",
  dr_test: "operations",
  vuln_scan: "operations",
  asset_inventory_review: "operations",

  custom: "other",
}

export function categoryFor(itemType: string): CategoryId {
  return TYPE_TO_CATEGORY[itemType] ?? "other"
}
