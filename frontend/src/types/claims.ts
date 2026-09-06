// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The biller export's refusal shape.
 *
 * Field names are snake_case to match `app.models.claims` exactly. When a
 * claim in an export would leave with a blocking scrub finding, the route
 * answers 422 with code `CLAIM_EXPORT_BLOCKED` and `details.claims` naming
 * each blocked claim and its findings.
 */

export interface ClaimFinding {
  severity: "blocking" | "warning"
  code: string
  message: string
  field: string | null
}

export interface ClaimExportFinding {
  claim_id: string
  control_number: string
  findings: ClaimFinding[]
}

export const CLAIM_EXPORT_BLOCKED = "CLAIM_EXPORT_BLOCKED"
