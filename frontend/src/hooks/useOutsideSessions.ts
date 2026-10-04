// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  answerOutsideSessions,
  getOutsideQuestions,
  listOutsideSessions,
  syncCalendarsNow,
  type CalendarSyncResult,
  type OutsideAnswer,
  type OutsideAnswerResult,
} from "@/lib/api/outsideSessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { calendarFreshness } from "./calendarFreshness"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Open events from the clinician's own calendar over a range. */
export function useOutsideSessions(start: string, end: string) {
  return useAuthQuery({
    queryKey: queryKeys.appointments.outsideSessions({ start, end }),
    queryFn: () => listOutsideSessions(start, end),
    staleTime: 60 * 1000,
    ...calendarFreshness,
    enabled: !!start && !!end,
  })
}

/** The "who is this?" questions still waiting, one per client. */
export function useOutsideQuestions() {
  return useAuthQuery({
    queryKey: queryKeys.appointments.outsideQuestions(),
    queryFn: () => getOutsideQuestions(),
    staleTime: 60 * 1000,
    ...calendarFreshness,
  })
}

export function useAnswerOutsideSessions() {
  return useAuthMutation<OutsideAnswerResult, OutsideAnswer[]>({
    mutationFn: (answers) => answerOutsideSessions(answers),
    // Appointments appear, open rows go, and a new client may join the list.
    invalidateKeys: [queryKeys.appointments.all, queryKeys.patients.all],
  })
}

/**
 * Read the clinician's calendars now rather than at the next scheduled pass.
 * A read can move, add or cancel sessions and raise new questions, so every
 * calendar query refetches once it succeeds.
 */
export function useSyncCalendarsNow() {
  return useAuthMutation<CalendarSyncResult, void>({
    mutationFn: () => syncCalendarsNow(),
    invalidateKeys: [queryKeys.appointments.all],
  })
}
