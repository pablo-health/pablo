// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Claims API client — the biller handoff. Wraps `app.routes.claims_export`.
 *
 * Both calls are downloads: the CSV of every validated claim dated in a
 * range, and one claim as a CMS-1500-layout PDF. A refusal (a claim that
 * would leave with a blocking finding) is an `ApiError` whose details name
 * the blocked claims; `blockedClaimsFrom` pulls that list out.
 */

import { CLAIM_EXPORT_BLOCKED, type ClaimExportFinding } from "@/types/claims"
import { ApiError, getBlob } from "./client"

const CLAIMS = "/api/claims"

/** ISO calendar dates (`YYYY-MM-DD`), both ends inclusive. */
export async function downloadClaimsCsv(
  from: string,
  to: string,
  token?: string,
): Promise<Blob> {
  const query = new URLSearchParams({ from, to })
  return getBlob(`${CLAIMS}/export.csv?${query.toString()}`, token)
}

export async function downloadClaimCms1500(claimId: string, token?: string): Promise<Blob> {
  return getBlob(`${CLAIMS}/${claimId}/cms1500.pdf`, token)
}

/** The claims an export refused, or `null` when the error is something else. */
export function blockedClaimsFrom(error: unknown): ClaimExportFinding[] | null {
  if (!(error instanceof ApiError) || error.code !== CLAIM_EXPORT_BLOCKED) return null
  const claims = error.details?.claims
  return Array.isArray(claims) ? (claims as ClaimExportFinding[]) : []
}
