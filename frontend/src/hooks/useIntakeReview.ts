// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"

import { ApiError } from "@/lib/api/client"
import {
  acceptIntakeAssignment,
  enterIntakeAnswerForPatient,
  getIntakeReview,
  requestIntakeCorrection,
  type IntakeAssignment,
  type IntakeReview,
} from "@/lib/api/intakeReview"

import { useAuthQuery } from "./useAuthQuery"

/**
 * Cache keys for the review read.
 *
 * Declared here rather than in the shared key factory because the review is
 * the only thing that reads or writes them; fold them into `queryKeys` when
 * a second surface needs to invalidate the same read.
 */
export const intakeReviewKeys = {
  all: ["intakeReview"] as const,
  detail: (patientId: string, assignmentId: string) =>
    [...intakeReviewKeys.all, patientId, assignmentId] as const,
}

/** A form as the clinician reviews it. */
export function useIntakeReview(
  patientId: string | undefined,
  assignmentId: string | undefined,
  token?: string,
) {
  return useAuthQuery<IntakeReview>({
    queryKey: intakeReviewKeys.detail(patientId ?? "", assignmentId ?? ""),
    queryFn: () => getIntakeReview(patientId ?? "", assignmentId ?? "", token),
    enabled: !!patientId && !!assignmentId,
  })
}

/**
 * The shared posture for the three writes on this screen.
 *
 * Written on `useMutation` directly rather than `useAuthMutation` for the
 * error path: a 409 means the server's copy of the form has already moved
 * on — somebody accepted it, or the patient handed it in — so the screen the
 * clinician is looking at is describing a form that no longer exists in that
 * state. Re-reading replaces it with what is actually there instead of
 * leaving a stale screen with an error stuck to it.
 */
function useReviewMutation<TVariables>(
  patientId: string,
  assignmentId: string,
  mutationFn: (variables: TVariables) => Promise<IntakeAssignment>,
) {
  const queryClient = useQueryClient()
  const queryKey = intakeReviewKeys.detail(patientId, assignmentId)

  return useMutation<IntakeAssignment, Error, TVariables>({
    mutationFn,
    onSuccess: () => queryClient.invalidateQueries({ queryKey }),
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        void queryClient.invalidateQueries({ queryKey })
      }
    },
  })
}

/** Reopen named questions, with a note saying what to redo. */
export function useRequestIntakeCorrection(
  patientId: string,
  assignmentId: string,
  token?: string,
) {
  return useReviewMutation<{ item_ids: string[]; note: string }>(
    patientId,
    assignmentId,
    (body) => requestIntakeCorrection(patientId, assignmentId, body, token),
  )
}

/** Accept the form. */
export function useAcceptIntakeAssignment(
  patientId: string,
  assignmentId: string,
  token?: string,
) {
  return useReviewMutation<void>(patientId, assignmentId, () =>
    acceptIntakeAssignment(patientId, assignmentId, token),
  )
}

/** Write an answer down for somebody in the room. */
export function useEnterIntakeAnswer(
  patientId: string,
  assignmentId: string,
  token?: string,
) {
  return useReviewMutation<{ itemId: string; value: Record<string, unknown> }>(
    patientId,
    assignmentId,
    ({ itemId, value }) =>
      enterIntakeAnswerForPatient(patientId, assignmentId, itemId, value, token),
  )
}
