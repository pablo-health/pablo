// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  addToWaitlist,
  getPatientWaitlist,
  getWaitlist,
  removeWaitlistEntry,
  reorderWaitlist,
  updateWaitlistEntry,
} from "@/lib/api/waitlist"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  AddWaitlistEntryRequest,
  PatientWaitlist,
  UpdateWaitlistEntryRequest,
  WaitlistEntry,
  WaitlistResponse,
} from "@/types/waitlist"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Every write can move the list and any client's place on it. */
const afterWrite = [queryKeys.waitlist.all] as const

/** The clinician's waitlist, top first. */
export function useWaitlist() {
  return useAuthQuery<WaitlistResponse>({
    queryKey: queryKeys.waitlist.list(),
    queryFn: () => getWaitlist(),
  })
}

/** One client's entry, if any, and what to suggest when adding them. */
export function usePatientWaitlist(patientId: string | undefined, enabled = true) {
  return useAuthQuery<PatientWaitlist>({
    queryKey: queryKeys.waitlist.byPatient(patientId ?? ""),
    queryFn: () => getPatientWaitlist(patientId!),
    enabled: !!patientId && enabled,
  })
}

export function useAddToWaitlist() {
  return useAuthMutation<WaitlistEntry, { patientId: string; data: AddWaitlistEntryRequest }>({
    mutationFn: ({ patientId, data }) => addToWaitlist(patientId, data),
    invalidateKeys: afterWrite,
  })
}

/** Send the whole list in its new order. A 409 means it changed underneath. */
export function useReorderWaitlist() {
  return useAuthMutation<WaitlistResponse, string[]>({
    mutationFn: (entryIds) => reorderWaitlist(entryIds),
    onSuccess: (data, _ids, queryClient) => {
      queryClient.setQueryData(queryKeys.waitlist.list(), data)
    },
    invalidateKeys: afterWrite,
  })
}

export function useUpdateWaitlistEntry() {
  return useAuthMutation<WaitlistEntry, { entryId: string; data: UpdateWaitlistEntryRequest }>({
    mutationFn: ({ entryId, data }) => updateWaitlistEntry(entryId, data),
    invalidateKeys: afterWrite,
  })
}

export function useRemoveWaitlistEntry() {
  return useAuthMutation<WaitlistEntry, string>({
    mutationFn: (entryId) => removeWaitlistEntry(entryId),
    invalidateKeys: afterWrite,
  })
}
