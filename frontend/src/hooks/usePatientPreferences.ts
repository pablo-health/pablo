// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  AddPreferenceRequest,
  Preference,
  PreferenceListResponse,
  UpdatePreferenceRequest,
} from "@/types/patientPreferences"
import {
  addPreference,
  listPreferences,
  retirePreference,
  updatePreference,
} from "@/lib/api/patientPreferences"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

function afterWrite({ patientId }: { patientId: string }) {
  return [queryKeys.patientPreferences.byPatient(patientId)]
}

export function usePatientPreferences(patientId: string | undefined) {
  return useAuthQuery<PreferenceListResponse>({
    queryKey: queryKeys.patientPreferences.byPatient(patientId ?? ""),
    queryFn: () => listPreferences(patientId!),
    enabled: !!patientId,
  })
}

export function useAddPreference() {
  return useAuthMutation<Preference, { patientId: string; data: AddPreferenceRequest }>({
    mutationFn: ({ patientId, data }) => addPreference(patientId, data),
    invalidateKeys: afterWrite,
  })
}

export function useUpdatePreference() {
  return useAuthMutation<
    Preference,
    { patientId: string; preferenceId: string; data: UpdatePreferenceRequest }
  >({
    mutationFn: ({ patientId, preferenceId, data }) =>
      updatePreference(patientId, preferenceId, data),
    invalidateKeys: afterWrite,
  })
}

export function useRetirePreference() {
  return useAuthMutation<Preference, { patientId: string; preferenceId: string }>({
    mutationFn: ({ patientId, preferenceId }) => retirePreference(patientId, preferenceId),
    invalidateKeys: afterWrite,
  })
}
