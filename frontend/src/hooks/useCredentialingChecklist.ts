// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getChecklist,
  listConfirmations,
  lookUpNpi,
  recordConfirmation,
  saveChecklistAnswers,
} from "@/lib/api/credentialing"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  ConfirmationPayload,
  ChecklistAnswers,
} from "@/types/credentialing"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The question set for this clinician, with what she has already answered.
 *
 * `branch` overrides what the record says, and the wizard passes it while she
 * is answering the supervision fork — the answer has to narrow the same page
 * it was given on, before anything is saved.
 */
export function useChecklist(
  branch?: { supervised?: boolean; prescriber?: boolean },
  token?: string,
) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.checklist(branch),
    queryFn: () => getChecklist(branch ?? {}, token),
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

export function useSaveChecklistAnswers(token?: string) {
  return useAuthMutation({
    mutationFn: (answers: ChecklistAnswers) => saveChecklistAnswers(answers, token),
    invalidateKeys: [queryKeys.credentialing.all],
  })
}

/**
 * The registry's record for one NPI.
 *
 * Disabled until the number is ten digits, so typing does not fire a lookup per
 * keystroke. `retry: false` because "not found" arrives as a normal answer —
 * there is nothing to retry — and retrying a genuine outage three times only
 * makes her wait longer for the same screen.
 */
export function useNpiLookup(npi: string | null, token?: string) {
  const ready = npi !== null && /^\d{10}$/.test(npi)
  return useAuthQuery({
    queryKey: queryKeys.credentialing.nppes(npi ?? ""),
    queryFn: () => lookUpNpi(npi as string, token),
    enabled: ready,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}
