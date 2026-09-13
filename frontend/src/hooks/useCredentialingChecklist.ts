// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  getChecklist,
  getPayerAuthorization,
  listConfirmations,
  listPanelApplications,
  lookUpNpi,
  readPayerAuthorization,
  revokePayerAuthorization,
  signPayerAuthorization,
  searchNpi,
  recordConfirmation,
  saveChecklistAnswers,
} from "@/lib/api/credentialing"
import { queryKeys } from "@/lib/api/queryKeys"
import type {
  ConfirmationPayload,
  ChecklistAnswers,
  NppesSearchQuery,
  SignPayerAuthorization,
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

/**
 * Providers matching a name. Runs only once a surname has been submitted —
 * searching per keystroke would hammer a public registry for no benefit.
 */
export function useNpiSearch(query: NppesSearchQuery | null, token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.nppesSearch(
      query ?? { last_name: "" },
    ),
    queryFn: () => searchNpi(query as NppesSearchQuery, token),
    enabled: query !== null && query.last_name.trim().length > 0,
    retry: false,
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Where her panel applications stand.
 *
 * Nothing on this screen is hers to change — Pablo moves these — so there is
 * no mutation beside it and no optimistic anything. It goes stale on its own
 * clock because the answer changes when a payer answers, which is on the order
 * of weeks.
 */
export function usePanelApplications(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.panelApplications(),
    queryFn: () => listPanelApplications(token),
    staleTime: 5 * 60 * 1000,
  })
}

/**
 * Whether Pablo may apply to panels on her behalf.
 *
 * No `staleTime`: this gates what Pablo is allowed to do with her name, so the
 * screen should re-ask rather than show a cached "signed" after she withdrew it
 * in another tab.
 */
export function usePayerAuthorization(token?: string) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.payerAuthorization(),
    queryFn: () => getPayerAuthorization(token),
  })
}

/**
 * The text of the authorisation. Only fetched once she asks to read it —
 * it is a full legal document, and nobody wants it in the page weight of a
 * screen they opened to check on an application.
 */
export function usePayerAuthorizationDocument(
  enabled: boolean,
  version?: string,
  token?: string,
) {
  return useAuthQuery({
    queryKey: queryKeys.credentialing.payerAuthorizationDocument(version),
    queryFn: () => readPayerAuthorization(version, token),
    enabled,
    staleTime: Infinity,
  })
}

export function useSignPayerAuthorization(token?: string) {
  return useAuthMutation({
    mutationFn: (payload: SignPayerAuthorization) =>
      signPayerAuthorization(payload, token),
    invalidateKeys: [queryKeys.credentialing.payerAuthorization()],
  })
}

export function useRevokePayerAuthorization(token?: string) {
  return useAuthMutation({
    mutationFn: () => revokePayerAuthorization(token),
    invalidateKeys: [queryKeys.credentialing.payerAuthorization()],
  })
}
