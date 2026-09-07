// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Claims API client. Wraps `app.routes.claims` (build, read, validate,
 * correct, void, the tracker), `app.routes.claim_status` (a status check on
 * demand) and `app.routes.claims_export` (the biller handoff).
 *
 * The two refusals a screen has to read are both `ApiError`s whose details
 * carry findings: a validation that found something blocking
 * (`blockingFindingsFrom`) and an export that would leave with one
 * (`blockedClaimsFrom`).
 */

import {
  CLAIM_EXPORT_BLOCKED,
  CLAIM_VALIDATION_FAILED,
  type BuildClaimRequest,
  type ClaimDetailResponse,
  type ClaimExportFinding,
  type ClaimFinding,
  type ClaimResponse,
  type ClaimTrackerFilters,
  type ClaimTrackerResponse,
  type ValidateClaimResponse,
} from "@/types/claims"
import { ApiError, get, getBlob, post } from "./client"

const CLAIMS = "/api/claims"

export async function listClaims(
  filters: ClaimTrackerFilters = {},
  token?: string,
): Promise<ClaimTrackerResponse> {
  const query = new URLSearchParams()
  if (filters.state) query.set("state", filters.state)
  if (filters.from) query.set("from", filters.from)
  if (filters.to) query.set("to", filters.to)
  const suffix = query.size > 0 ? `?${query.toString()}` : ""
  return get<ClaimTrackerResponse>(`${CLAIMS}${suffix}`, token)
}

export async function fetchClaim(claimId: string, token?: string): Promise<ClaimDetailResponse> {
  return get<ClaimDetailResponse>(`${CLAIMS}/${claimId}`, token)
}

/** A draft claim for the visit, snapshotted from what is on file now. */
export async function buildClaimFromSession(
  appointmentId: string,
  data: BuildClaimRequest = {},
  token?: string,
): Promise<ClaimResponse> {
  return post<ClaimResponse>(`${CLAIMS}/from-session/${appointmentId}`, data, token)
}

/** Run the scrub; a clean claim becomes `validated`, a blocked one stays a draft. */
export async function validateClaim(
  claimId: string,
  token?: string,
): Promise<ValidateClaimResponse> {
  return post<ValidateClaimResponse>(`${CLAIMS}/${claimId}/validate`, {}, token)
}

/** A replacement claim, rebuilt from today's sources, with this one as its parent. */
export async function correctClaim(claimId: string, token?: string): Promise<ClaimResponse> {
  return post<ClaimResponse>(`${CLAIMS}/${claimId}/correct`, {}, token)
}

/** A void of this claim: the same claim restated with frequency `8`. */
export async function voidClaim(claimId: string, token?: string): Promise<ClaimResponse> {
  return post<ClaimResponse>(`${CLAIMS}/${claimId}/void`, {}, token)
}

/**
 * Ask the clearinghouse about this claim now and read back where it stands.
 *
 * A practice with no clearinghouse configured, or one that is not answering,
 * is a 503 whose message says which — see `clearinghouseUnavailable`.
 */
export async function checkClaimStatus(
  claimId: string,
  token?: string,
): Promise<ClaimDetailResponse> {
  return post<ClaimDetailResponse>(`${CLAIMS}/${claimId}/status`, {}, token)
}

/**
 * The route's own words for a status check it could not run, or `null` when
 * the error is something else. A deployment fact rather than a fault: there is
 * nothing about the claim for the clinician to fix.
 */
export function clearinghouseUnavailable(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 503) return null
  return error.message
}

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

/** The findings that stopped a validation, or `null` when the error is something else. */
export function blockingFindingsFrom(error: unknown): ClaimFinding[] | null {
  if (!(error instanceof ApiError) || error.code !== CLAIM_VALIDATION_FAILED) return null
  const findings = error.details?.findings
  return Array.isArray(findings) ? (findings as ClaimFinding[]) : []
}

/** The claims an export refused, or `null` when the error is something else. */
export function blockedClaimsFrom(error: unknown): ClaimExportFinding[] | null {
  if (!(error instanceof ApiError) || error.code !== CLAIM_EXPORT_BLOCKED) return null
  const claims = error.details?.claims
  return Array.isArray(claims) ? (claims as ClaimExportFinding[]) : []
}
