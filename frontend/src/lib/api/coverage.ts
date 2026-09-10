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
  EnrollmentDocumentUrlResponse,
  EnrollmentTaskListResponse,
  PayerEnrollmentListResponse,
  PayerEnrollmentRefreshResponse,
  PayerListResponse,
  PayerResponse,
  UpdateCoverageRequest,
  UpdatePayerRequest,
} from "@/types/coverage"
import { ApiError, del, get, patch, post, postForm } from "./client"

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

export async function listPayerEnrollments(
  payerRowId: string,
  token?: string,
): Promise<PayerEnrollmentListResponse> {
  return get<PayerEnrollmentListResponse>(`${PAYERS}/${payerRowId}/enrollments`, token)
}

/** Enroll with the payer: files whatever it needs that is not on file yet. */
export async function requestPayerEnrollments(
  payerRowId: string,
  token?: string,
): Promise<PayerEnrollmentListResponse> {
  return post<PayerEnrollmentListResponse>(`${PAYERS}/${payerRowId}/enrollments`, {}, token)
}

function enrollment(payerRowId: string, transactionType: string): string {
  return `${PAYERS}/${payerRowId}/enrollments/${transactionType}`
}

/** What the payer is waiting on, read live from the clearinghouse. */
export async function listEnrollmentTasks(
  payerRowId: string,
  transactionType: string,
  token?: string,
): Promise<EnrollmentTaskListResponse> {
  return get<EnrollmentTaskListResponse>(`${enrollment(payerRowId, transactionType)}/tasks`, token)
}

/**
 * Answer one task: the typed values as JSON, each PDF paired with the field
 * key it answers. Whole or not at all — a half-answered task is refused
 * before anything is sent.
 */
export async function answerEnrollmentTask(
  payerRowId: string,
  transactionType: string,
  taskId: string,
  answer: { values: Record<string, string>; documents: Record<string, File> },
  token?: string,
): Promise<EnrollmentTaskListResponse> {
  const form = new FormData()
  form.append("values", JSON.stringify(answer.values))
  for (const [key, file] of Object.entries(answer.documents)) {
    form.append("document_fields", key)
    form.append("documents", file)
  }
  return postForm<EnrollmentTaskListResponse>(
    `${enrollment(payerRowId, transactionType)}/tasks/${taskId}`,
    form,
    token,
  )
}

/** A short-lived URL for one of the enrollment's PDFs, to open or download. */
export async function getEnrollmentDocumentUrl(
  payerRowId: string,
  transactionType: string,
  documentId: string,
  token?: string,
): Promise<EnrollmentDocumentUrlResponse> {
  return get<EnrollmentDocumentUrlResponse>(
    `${enrollment(payerRowId, transactionType)}/documents/${documentId}`,
    token,
  )
}

/**
 * Check every open enrollment request across every payer in one pass.
 *
 * A press inside the server's throttle floor answers with the previous
 * pass's result (``throttled: true``) instead of a fresh vendor call.
 */
export async function refreshPayerEnrollments(
  token?: string,
): Promise<PayerEnrollmentRefreshResponse> {
  return post<PayerEnrollmentRefreshResponse>(`${PAYERS}/enrollments/refresh`, {}, token)
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
