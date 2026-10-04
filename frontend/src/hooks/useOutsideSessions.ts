// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { cancelAppointment } from "@/lib/api/scheduling"
import {
  answerOutsideSessions,
  getOutsideQuestions,
  listAutoBooked,
  listOutsideSessions,
  acknowledgeAutoBooked,
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

/** Sessions booked automatically from a title, not yet acknowledged. */
export function useAutoBooked() {
  return useAuthQuery({
    queryKey: queryKeys.appointments.autoBooked(),
    queryFn: () => listAutoBooked(),
    staleTime: 60 * 1000,
  })
}

/** Undo one: the ordinary cancel, so the next read leaves it cancelled. */
export function useUndoAutoBooked() {
  return useAuthMutation<unknown, string>({
    mutationFn: (appointmentId) => cancelAppointment(appointmentId),
    invalidateKeys: [queryKeys.appointments.all],
  })
}

/** The clinician has looked; the list clears and the sessions stay booked. */
export function useAcknowledgeAutoBooked() {
  return useAuthMutation<{ acknowledged: number }, string[]>({
    mutationFn: (appointmentIds) => acknowledgeAutoBooked(appointmentIds),
    invalidateKeys: [queryKeys.appointments.autoBooked()],
  })
}

export function useAnswerOutsideSessions() {
  return useAuthMutation<OutsideAnswerResult, OutsideAnswer[]>({
    mutationFn: (answers) => answerOutsideSessions(answers),
    // Appointments appear, open rows go, and a new client may join the list.
    invalidateKeys: [queryKeys.appointments.all, queryKeys.patients.all],
  })
}
