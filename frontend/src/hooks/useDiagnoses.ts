// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CreateDiagnosticAssessmentRequest,
  DiagnosticAssessment,
  DiagnosticAssessmentListResponse,
  DiagnosticDefinitionListResponse,
} from "@/types/diagnoses"
import {
  createDiagnosticAssessment,
  deleteDiagnosticAssessment,
  listDiagnosticAssessments,
  listDiagnosticDefinitions,
} from "@/lib/api/diagnoses"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The global diagnostic-definition catalog used to render criterion forms.
 * Shared across patients and rarely changes, so it's cached generously.
 */
export function useDiagnosticDefinitions(token?: string) {
  return useAuthQuery<DiagnosticDefinitionListResponse>({
    queryKey: queryKeys.diagnoses.definitions,
    queryFn: () => listDiagnosticDefinitions(token),
    staleTime: 5 * 60_000,
  })
}

/**
 * List a patient's diagnostic assessments, optionally filtered to one
 * definition. Rows come back ordered by `assessed_at` ascending.
 */
export function usePatientDiagnoses(
  patientId: string | undefined,
  instrument?: string,
  token?: string,
) {
  return useAuthQuery<DiagnosticAssessmentListResponse>({
    queryKey: queryKeys.diagnoses.byPatient(patientId ?? "", instrument),
    queryFn: () => listDiagnosticAssessments(patientId!, instrument, token),
    enabled: !!patientId,
  })
}

export function useCreateDiagnosis(token?: string) {
  return useAuthMutation<
    DiagnosticAssessment,
    { patientId: string; data: CreateDiagnosticAssessmentRequest }
  >({
    mutationFn: ({ patientId, data }) =>
      createDiagnosticAssessment(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.diagnoses.byPatientAll(patientId),
    ],
  })
}

export function useDeleteDiagnosis(token?: string) {
  return useAuthMutation<
    void,
    { assessmentId: string; patientId: string }
  >({
    mutationFn: ({ assessmentId }) =>
      deleteDiagnosticAssessment(assessmentId, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.diagnoses.byPatientAll(patientId),
    ],
  })
}
