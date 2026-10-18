// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  fetchPracticeAdmins,
  grantPracticeAdmin,
  revokePracticeAdmin,
  type PracticeAdminsResponse,
} from "@/lib/api/practiceAdmins"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** The practice's clinicians and who is an admin (practice owner only). */
export function usePracticeAdmins(enabled: boolean) {
  return useAuthQuery<PracticeAdminsResponse>({
    queryKey: queryKeys.practiceAdmins.all,
    queryFn: () => fetchPracticeAdmins(),
    enabled,
  })
}

interface SetPracticeAdminVariables {
  userId: string
  isAdmin: boolean
}

/** Make a clinician an admin, or stop them being one. */
export function useSetPracticeAdmin() {
  return useAuthMutation<PracticeAdminsResponse, SetPracticeAdminVariables>({
    mutationFn: ({ userId, isAdmin }) =>
      isAdmin ? grantPracticeAdmin(userId) : revokePracticeAdmin(userId),
    invalidateKeys: [queryKeys.practiceAdmins.all],
  })
}
