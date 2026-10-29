// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { confirmPsychotherapyWindow, getVisitTimes } from "@/lib/api/visitTimes"
import { queryKeys } from "@/lib/api/queryKeys"
import { visitPdfLines } from "@/lib/notePdf"
import { usePeopleTerm } from "./usePeopleTerm"
import { useUserTimeZone } from "./usePreferences"
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

/** The visit's times as a note's PDF prints them; none for a note with no session. */
export function useVisitPdfLines(sessionId: string | null | undefined): string[] {
  const timeZone = useUserTimeZone()
  const people = usePeopleTerm()
  const { data } = useVisitTimes(sessionId ?? "", !!sessionId)
  return data ? visitPdfLines(data, timeZone, people) : []
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
