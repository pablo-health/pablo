// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  CreateMedicationRequest,
  Medication,
  MedicationListResponse,
  UpdateMedicationRequest,
} from "@/types/medications"
import {
  createMedication,
  deleteMedication,
  listMedications,
  updateMedication,
} from "@/lib/api/medications"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * List all medications for a patient. Results include every status by default;
 * the caller can pass `status` to filter to active-only or similar.
 */
export function usePatientMedications(
  patientId: string | undefined,
  token?: string,
) {
  return useAuthQuery<MedicationListResponse>({
    queryKey: queryKeys.medications.byPatient(patientId ?? ""),
    queryFn: () => listMedications(patientId!, undefined, token),
    enabled: !!patientId,
  })
}

export function useCreateMedication(token?: string) {
  return useAuthMutation<
    Medication,
    { patientId: string; data: CreateMedicationRequest }
  >({
    mutationFn: ({ patientId, data }) =>
      createMedication(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.medications.byPatientAll(patientId),
    ],
  })
}

export function useUpdateMedication(token?: string) {
  return useAuthMutation<
    Medication,
    { patientId: string; medicationId: string; data: UpdateMedicationRequest }
  >({
    mutationFn: ({ patientId, medicationId, data }) =>
      updateMedication(patientId, medicationId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.medications.byPatientAll(patientId),
    ],
  })
}

export function useDeleteMedication(token?: string) {
  return useAuthMutation<
    void,
    { patientId: string; medicationId: string }
  >({
    mutationFn: ({ patientId, medicationId }) =>
      deleteMedication(patientId, medicationId, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.medications.byPatientAll(patientId),
    ],
  })
}
