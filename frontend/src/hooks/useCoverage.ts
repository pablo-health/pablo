// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CoverageResponse,
  CreateCoverageRequest,
  CreatePayerRequest,
  EnrollmentTaskListResponse,
  PayerEnrollmentListResponse,
  PayerEnrollmentRefreshResponse,
  PayerListResponse,
  PayerResponse,
  UpdateCoverageRequest,
  UpdatePayerRequest,
} from "@/types/coverage"
import {
  answerEnrollmentTask,
  createCoverage,
  createPayer,
  deactivateCoverage,
  fetchCoverage,
  listEnrollmentTasks,
  listPayerEnrollments,
  listPayers,
  refreshPayerEnrollments,
  requestPayerEnrollments,
  updateCoverage,
  updatePayer,
  verifyCoverage,
  searchPayerDirectory,
} from "@/lib/api/coverage"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

export function usePayers(token?: string) {
  return useAuthQuery<PayerListResponse>({
    queryKey: queryKeys.payers.list(),
    queryFn: () => listPayers(token),
    staleTime: 60 * 1000,
  })
}

export function useCreatePayer(token?: string) {
  return useAuthMutation<PayerResponse, CreatePayerRequest>({
    mutationFn: (data) => createPayer(data, token),
    invalidateKeys: [queryKeys.payers.all],
  })
}

export function useUpdatePayer(token?: string) {
  return useAuthMutation<PayerResponse, { id: string; data: UpdatePayerRequest }>({
    mutationFn: ({ id, data }) => updatePayer(id, data, token),
    // A payer edit shows on every coverage that names it.
    invalidateKeys: [queryKeys.payers.all, queryKeys.coverage.all],
  })
}

/** Where the practice stands with one payer, per transaction. Fetched when a
 * payer row is open, not for the whole list. */
export function usePayerEnrollments(payerRowId: string | undefined, token?: string) {
  return useAuthQuery<PayerEnrollmentListResponse>({
    queryKey: queryKeys.payers.enrollments(payerRowId ?? ""),
    queryFn: () => listPayerEnrollments(payerRowId!, token),
    enabled: !!payerRowId,
    staleTime: 60 * 1000,
  })
}

export function useRequestPayerEnrollments(token?: string) {
  return useAuthMutation<PayerEnrollmentListResponse, { payerRowId: string }>({
    mutationFn: ({ payerRowId }) => requestPayerEnrollments(payerRowId, token),
    // The payer row's overall status changes with its requests.
    invalidateKeys: ({ payerRowId }) => [
      queryKeys.payers.enrollments(payerRowId),
      queryKeys.payers.list(),
    ],
  })
}

/**
 * What the payer is waiting on for one transaction — the form to fill in.
 *
 * Read live from the clearinghouse, so it is not cached for long: the answer
 * changes when somebody at the payer's end acts, not when we do.
 */
export function useEnrollmentTasks(
  payerRowId: string | undefined,
  transactionType: string | undefined,
  token?: string,
) {
  return useAuthQuery<EnrollmentTaskListResponse>({
    queryKey: queryKeys.payers.enrollmentTasks(payerRowId ?? "", transactionType ?? ""),
    queryFn: () => listEnrollmentTasks(payerRowId!, transactionType!, token),
    enabled: !!payerRowId && !!transactionType,
    staleTime: 0,
  })
}

export function useAnswerEnrollmentTask(token?: string) {
  return useAuthMutation<
    EnrollmentTaskListResponse,
    {
      payerRowId: string
      transactionType: string
      taskId: string
      values: Record<string, string>
      documents: Record<string, File>
    }
  >({
    mutationFn: ({ payerRowId, transactionType, taskId, values, documents }) =>
      answerEnrollmentTask(payerRowId, transactionType, taskId, { values, documents }, token),
    // Answering can move the request, and the request moves the payer row.
    invalidateKeys: ({ payerRowId, transactionType }) => [
      queryKeys.payers.enrollmentTasks(payerRowId, transactionType),
      queryKeys.payers.enrollments(payerRowId),
      queryKeys.payers.list(),
    ],
  })
}

/** The "check for updates" button: one pass across every payer's open requests. */
export function useRefreshPayerEnrollments(token?: string) {
  return useAuthMutation<PayerEnrollmentRefreshResponse, void>({
    mutationFn: () => refreshPayerEnrollments(token),
    // Any request could have moved, so every payer's status and detail may have too.
    invalidateKeys: () => [queryKeys.payers.all],
  })
}

/** The client's coverage on file, or `null` when there is none. Not retried:
 * "nothing on file" is already a `null`, not an error. */
export function usePatientCoverage(patientId: string | undefined, token?: string) {
  return useAuthQuery<CoverageResponse | null>({
    queryKey: queryKeys.coverage.byPatient(patientId ?? ""),
    queryFn: () => fetchCoverage(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })
}

export function useCreateCoverage(token?: string) {
  return useAuthMutation<CoverageResponse, { patientId: string; data: CreateCoverageRequest }>({
    mutationFn: ({ patientId, data }) => createCoverage(patientId, data, token),
    // A typed-in payer lands on the list too.
    invalidateKeys: ({ patientId }) => [
      queryKeys.coverage.byPatient(patientId),
      queryKeys.payers.all,
    ],
  })
}

export function useUpdateCoverage(token?: string) {
  return useAuthMutation<CoverageResponse, { patientId: string; data: UpdateCoverageRequest }>({
    mutationFn: ({ patientId, data }) => updateCoverage(patientId, data, token),
    invalidateKeys: ({ patientId }) => [queryKeys.coverage.byPatient(patientId)],
  })
}

export function useDeactivateCoverage(token?: string) {
  return useAuthMutation<void, { patientId: string }>({
    mutationFn: ({ patientId }) => deactivateCoverage(patientId, token),
    invalidateKeys: ({ patientId }) => [queryKeys.coverage.byPatient(patientId)],
  })
}

/** The chart card's re-verify button: ask the payer now. */
export function useVerifyCoverage(token?: string) {
  return useAuthMutation<CoverageResponse, { patientId: string }>({
    mutationFn: ({ patientId }) => verifyCoverage(patientId, token),
    invalidateKeys: ({ patientId }) => [queryKeys.coverage.byPatient(patientId)],
  })
}

/**
 * Payers matching a name in the clearinghouse directory.
 *
 * Runs only once a term has been submitted — searching per keystroke would ask
 * a vendor directory a question per character for no benefit. `retry: false`
 * because an unreachable clearinghouse comes back as `unavailable: true`, which
 * is an answer the screen renders rather than a failure worth repeating.
 */
export function usePayerDirectory(query: string | null, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.payers.directory(query ?? ""),
    queryFn: () => searchPayerDirectory(query as string, token),
    enabled: query !== null && query.trim().length > 0,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}
