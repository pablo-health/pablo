// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CreateOutcomeMeasureRequest,
  OutcomeMeasure,
  OutcomeMeasureListResponse,
} from "@/types/outcomeMeasures"
import {
  createOutcomeMeasure,
  deleteOutcomeMeasure,
  listOutcomeMeasures,
} from "@/lib/api/outcomeMeasures"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * List a patient's outcome measures, optionally filtered to one instrument
 * (the trend read). Rows come back ordered by `administered_at` ascending.
 */
export function usePatientOutcomeMeasures(
  patientId: string | undefined,
  instrument?: string,
  token?: string,
) {
  return useAuthQuery<OutcomeMeasureListResponse>({
    queryKey: queryKeys.outcomeMeasures.byPatient(patientId ?? "", instrument),
    queryFn: () => listOutcomeMeasures(patientId!, instrument, token),
    enabled: !!patientId,
  })
}

export function useCreateOutcomeMeasure(token?: string) {
  return useAuthMutation<
    OutcomeMeasure,
    { patientId: string; data: CreateOutcomeMeasureRequest }
  >({
    mutationFn: ({ patientId, data }) =>
      createOutcomeMeasure(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.outcomeMeasures.byPatientAll(patientId),
    ],
  })
}

export function useDeleteOutcomeMeasure(token?: string) {
  return useAuthMutation<
    void,
    { measureId: string; patientId: string }
  >({
    mutationFn: ({ measureId }) => deleteOutcomeMeasure(measureId, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.outcomeMeasures.byPatientAll(patientId),
    ],
  })
}
