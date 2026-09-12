// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getIntake,
  listConfirmations,
  recordConfirmation,
  saveIntakeAnswers,
} from "@/lib/api/credentialing"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  ConfirmationPayload,
  IntakeAnswers,
} from "@/types/credentialing"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The question set for this clinician, with what she has already answered.
 *
 * `branch` overrides what the record says, and the wizard passes it while she
 * is answering the supervision fork — the answer has to narrow the same page
 * it was given on, before anything is saved.
 */
export function useIntake(
  branch?: { supervised?: boolean; prescriber?: boolean },
  token?: string,
) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.intake(branch),
    queryFn: () => getIntake(branch ?? {}, token),
  })
}

export function useConfirmations(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.confirmations(),
    queryFn: () => listConfirmations(token),
  })
}

export function useRecordConfirmation(token?: string) {
  return useAuthMutation({
    mutationFn: ({
      fieldKey,
      payload,
    }: {
      fieldKey: string
      payload: ConfirmationPayload
    }) => recordConfirmation(fieldKey, payload, token),
    // Both: a confirmation is a row of its own AND it can be what makes a
    // Tier-0 field answered, so the progress readout is stale without it.
    invalidateKeys: [
      queryKeys.credentialing.confirmations(),
      queryKeys.credentialing.all,
    ],
  })
}

export function useSaveIntakeAnswers(token?: string) {
  return useAuthMutation({
    mutationFn: (answers: IntakeAnswers) => saveIntakeAnswers(answers, token),
    invalidateKeys: [queryKeys.credentialing.all],
  })
}
