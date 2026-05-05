// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQuery } from "@tanstack/react-query"
import type {
  CreatePatientRequest,
  PatientListParams,
  PatientResponse,
  UpdatePatientRequest,
} from "@/types/patients"
import {
  createPatient,
  deletePatient,
  getPatient,
  listPatients,
  restorePatient,
  updatePatient,
} from "@/lib/api/patients"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery, useAuthMutation } from "./useAuthQuery"

export function usePatientList(params?: PatientListParams, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.patients.list(params),
    queryFn: () => listPatients(params, token),
    staleTime: 60 * 1000,
  })
}

export function usePatient(
  patientId: string,
  token?: string,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: queryKeys.patients.detail(patientId),
    queryFn: () => getPatient(patientId, token),
    staleTime: 60 * 1000,
    ...options,
  })
}

export function useCreatePatient(token?: string) {
  return useAuthMutation({
    mutationFn: (data: CreatePatientRequest) => createPatient(data, token),
    invalidateKeys: [queryKeys.patients.lists()],
  })
}

export function useUpdatePatient(token?: string) {
  return useAuthMutation<
    PatientResponse,
    { patientId: string; data: UpdatePatientRequest },
    PatientResponse
  >({
    mutationFn: ({ patientId, data }) => updatePatient(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.patients.detail(patientId),
      queryKeys.patients.lists(),
    ],
    optimistic: {
      queryKey: ({ patientId }) => queryKeys.patients.detail(patientId),
      updater: (previous, { data }) => ({ ...previous, ...data }),
    },
  })
}

export interface DeletePatientVariables {
  patientId: string
  acknowledgedRetentionObligation: boolean
}

export function useDeletePatient(token?: string) {
  return useAuthMutation({
    mutationFn: ({ patientId, acknowledgedRetentionObligation }: DeletePatientVariables) =>
      deletePatient(patientId, acknowledgedRetentionObligation, token),
    invalidateKeys: [queryKeys.patients.lists(), queryKeys.sessions.all],
    onSuccess: (_data, { patientId }, queryClient) => {
      queryClient.removeQueries({
        queryKey: queryKeys.patients.detail(patientId),
      })
    },
  })
}

/**
 * Restore a soft-deleted patient (THERAPY-yg2).
 *
 * Invalidates both the live and recently-deleted listings so the patient
 * row hops from the "Recently deleted" tab back to the live tab.
 */
export function useRestorePatient(token?: string) {
  return useAuthMutation({
    mutationFn: (patientId: string) => restorePatient(patientId, token),
    invalidateKeys: [queryKeys.patients.lists(), queryKeys.sessions.all],
  })
}
