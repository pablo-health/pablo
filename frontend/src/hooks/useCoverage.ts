// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CoverageResponse,
  CreateCoverageRequest,
  CreatePayerRequest,
  PayerEnrollmentListResponse,
  PayerListResponse,
  PayerResponse,
  UpdateCoverageRequest,
  UpdatePayerRequest,
} from "@/types/coverage"
import {
  createCoverage,
  createPayer,
  deactivateCoverage,
  fetchCoverage,
  listPayerEnrollments,
  listPayers,
  requestPayerEnrollments,
  updateCoverage,
  updatePayer,
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
