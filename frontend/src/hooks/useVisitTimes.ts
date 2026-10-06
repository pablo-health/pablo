// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { confirmPsychotherapyWindow, getVisitTimes } from "@/lib/api/visitTimes"
import { queryKeys } from "@/lib/api/queryKeys"
import type { ConfirmPsychotherapyWindowRequest, VisitTimes } from "@/types/visitTimes"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Under the session detail key, so whatever refreshes the session refreshes these. */
const visitTimesKey = (sessionId: string) =>
  [...queryKeys.sessions.detail(sessionId), "visit-times"] as const

export function useVisitTimes(sessionId: string, enabled = true) {
  return useAuthQuery<VisitTimes>({
    queryKey: visitTimesKey(sessionId),
    queryFn: () => getVisitTimes(sessionId),
    enabled,
  })
}

export function useConfirmPsychotherapyWindow(sessionId: string) {
  return useAuthMutation<VisitTimes, ConfirmPsychotherapyWindowRequest>({
    mutationFn: (data) => confirmPsychotherapyWindow(sessionId, data),
    onSuccess: (data, _variables, queryClient) => {
      queryClient.setQueryData(visitTimesKey(sessionId), data)
    },
    // The note's psychotherapy time field changes with the confirmation.
    invalidateKeys: [queryKeys.sessions.detail(sessionId)],
  })
}
