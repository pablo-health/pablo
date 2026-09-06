// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Coverage on file API client.
 *
 * Wraps `app.routes.coverage`: the practice's payer list, and one client's
 * plan on the chart.
 */

import type {
  CoverageResponse,
  CreateCoverageRequest,
  CreatePayerRequest,
  PayerListResponse,
  PayerResponse,
  UpdateCoverageRequest,
  UpdatePayerRequest,
} from "@/types/coverage"
import { ApiError, del, get, patch, post } from "./client"

const PAYERS = "/api/payers"

export async function listPayers(token?: string): Promise<PayerListResponse> {
  return get<PayerListResponse>(PAYERS, token)
}

export async function createPayer(
  data: CreatePayerRequest,
  token?: string,
): Promise<PayerResponse> {
  return post<PayerResponse>(PAYERS, data, token)
}

export async function updatePayer(
  payerRowId: string,
  data: UpdatePayerRequest,
  token?: string,
): Promise<PayerResponse> {
  return patch<PayerResponse>(`${PAYERS}/${payerRowId}`, data, token)
}

/**
 * The client's coverage on file, or `null` when there is none.
 *
 * The route answers "nothing on file" with a 404, matching its unknown-client
 * shape. That is a normal answer here rather than a failure, so it is
 * translated once.
 */
export async function fetchCoverage(
  patientId: string,
  token?: string,
): Promise<CoverageResponse | null> {
  try {
    return await get<CoverageResponse>(`/api/patients/${patientId}/coverage`, token)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export async function createCoverage(
  patientId: string,
  data: CreateCoverageRequest,
  token?: string,
): Promise<CoverageResponse> {
  return post<CoverageResponse>(`/api/patients/${patientId}/coverage`, data, token)
}

export async function updateCoverage(
  patientId: string,
  data: UpdateCoverageRequest,
  token?: string,
): Promise<CoverageResponse> {
  return patch<CoverageResponse>(`/api/patients/${patientId}/coverage`, data, token)
}

/** Take the plan off file. The row is deactivated server-side, not deleted. */
export async function deactivateCoverage(patientId: string, token?: string): Promise<void> {
  return del<void>(`/api/patients/${patientId}/coverage`, token)
}

/**
 * Run an eligibility check now, through the practice's own clearinghouse
 * account, and get the coverage back with the answer on it.
 *
 * 409 means the practice cannot ask yet (no clearinghouse account, no NPI, a
 * payer with no electronic id); the detail says which.
 */
export async function verifyCoverage(
  patientId: string,
  token?: string,
): Promise<CoverageResponse> {
  return post<CoverageResponse>(`/api/patients/${patientId}/coverage/verify`, {}, token)
}
