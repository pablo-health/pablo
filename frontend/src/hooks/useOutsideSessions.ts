// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { cancelAppointment } from "@/lib/api/scheduling"
import {
  answerOutsideSessions,
  getOutsideQuestions,
  listBookedOnItsOwn,
  listOutsideSessions,
  markBookedOnItsOwnSeen,
  type OutsideAnswer,
  type OutsideAnswerResult,
} from "@/lib/api/outsideSessions"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/** Open events from the clinician's own calendar over a range. */
export function useOutsideSessions(start: string, end: string) {
  return useAuthQuery({
    queryKey: queryKeys.appointments.outsideSessions({ start, end }),
    queryFn: () => listOutsideSessions(start, end),
    staleTime: 60 * 1000,
    enabled: !!start && !!end,
  })
}

/** The "who is this?" questions still waiting, one per client. */
export function useOutsideQuestions() {
  return useAuthQuery({
    queryKey: queryKeys.appointments.outsideQuestions(),
    queryFn: () => getOutsideQuestions(),
    staleTime: 60 * 1000,
  })
}

/** Sessions Pablo booked on its own from a title, not yet seen. */
export function useBookedOnItsOwn() {
  return useAuthQuery({
    queryKey: queryKeys.appointments.bookedOnItsOwn(),
    queryFn: () => listBookedOnItsOwn(),
    staleTime: 60 * 1000,
  })
}

/** Undo one: the ordinary cancel, so the next read leaves it cancelled. */
export function useUndoBookedOnItsOwn() {
  return useAuthMutation<unknown, string>({
    mutationFn: (appointmentId) => cancelAppointment(appointmentId),
    invalidateKeys: [queryKeys.appointments.all],
  })
}

/** The clinician has looked; the list clears and the sessions stay booked. */
export function useSeenBookedOnItsOwn() {
  return useAuthMutation<{ seen: number }, string[]>({
    mutationFn: (appointmentIds) => markBookedOnItsOwnSeen(appointmentIds),
    invalidateKeys: [queryKeys.appointments.bookedOnItsOwn()],
  })
}

export function useAnswerOutsideSessions() {
  return useAuthMutation<OutsideAnswerResult, OutsideAnswer[]>({
    mutationFn: (answers) => answerOutsideSessions(answers),
    // Appointments appear, open rows go, and a new client may join the list.
    invalidateKeys: [queryKeys.appointments.all, queryKeys.patients.all],
  })
}
