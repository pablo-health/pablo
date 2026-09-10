// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type {
  BalanceResponse,
  CardOnFileResponse,
  CardSetupResponse,
  ChargeAmountResponse,
  ChargeResponse,
  CreateChargeRequest,
  CreateWriteOffRequest,
} from "@/types/payments"
import {
  chargeBalance,
  completeCardSetup,
  createCharge,
  createWriteOff,
  fetchCardOnFile,
  fetchChargeAmount,
  fetchPatientBalance,
  listCharges,
  startCardSetup,
} from "@/lib/api/payments"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"

/**
 * The card on file for a client, or `null` when there is none.
 *
 * Not retried: the two things that go wrong here are "no card" (already a
 * `null`, not an error) and "this deployment has no card processing
 * configured", which is a settled fact rather than a blip.
 */
export function usePatientCard(patientId: string | undefined, token?: string) {
  return useAuthQuery<CardOnFileResponse | null>({
    queryKey: queryKeys.payments.card(patientId ?? ""),
    queryFn: () => fetchCardOnFile(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })
}

/**
 * What a charge would come to, so the clinician sees the figure before
 * authorising it. `amount_cents` is `null` when no rate is set anywhere.
 */
export function useChargeAmount(
  patientId: string | undefined,
  appointmentId?: string,
  token?: string,
) {
  return useAuthQuery<ChargeAmountResponse>({
    queryKey: queryKeys.payments.amount(patientId ?? "", appointmentId),
    queryFn: () => fetchChargeAmount(patientId!, appointmentId, token),
    enabled: !!patientId,
    retry: false,
  })
}

export function usePatientCharges(patientId: string | undefined, token?: string) {
  return useAuthQuery<ChargeResponse[]>({
    queryKey: queryKeys.payments.charges(patientId ?? ""),
    queryFn: () => listCharges(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })
}

/**
 * What the client owes, totalled on the server from their ledger.
 *
 * Retried, unlike the card reads beside it: this route is not gated on the
 * card processor, so a failure here is a real failure rather than the settled
 * fact that a deployment takes no cards.
 */
export function usePatientBalance(patientId: string | undefined, token?: string) {
  return useAuthQuery<BalanceResponse>({
    queryKey: queryKeys.payments.balance(patientId ?? ""),
    queryFn: () => fetchPatientBalance(patientId!, token),
    enabled: !!patientId,
  })
}

/**
 * Mint a SetupIntent and the Stripe.js configuration that goes with it.
 *
 * Invalidates nothing: no card exists yet, and one only will once the browser
 * confirms and `useCompleteCardSetup` records what got attached.
 */
export function useStartCardSetup(token?: string) {
  return useAuthMutation<CardSetupResponse, { patientId: string }>({
    mutationFn: ({ patientId }) => startCardSetup(patientId, token),
  })
}

export function useCompleteCardSetup(token?: string) {
  return useAuthMutation<
    CardOnFileResponse,
    { patientId: string; setupIntentId: string }
  >({
    mutationFn: ({ patientId, setupIntentId }) =>
      completeCardSetup(patientId, setupIntentId, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.payments.byPatientAll(patientId),
    ],
  })
}

/**
 * Charge the card on file.
 *
 * A decline resolves rather than rejecting — the row comes back `failed` with
 * the reason on it — so callers read `data.status`, and the ledger is
 * invalidated either way because a declined attempt is still a row on it.
 */
export function useCreateCharge(token?: string) {
  return useAuthMutation<
    ChargeResponse,
    { patientId: string; data: CreateChargeRequest }
  >({
    mutationFn: ({ patientId, data }) => createCharge(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.payments.charges(patientId),
      queryKeys.payments.balance(patientId),
    ],
  })
}

/**
 * Charge the card on file for the whole balance.
 *
 * Invalidates the balance as well as the ledger: the point of the action is
 * that the figure in the chart header changes, and leaving it stale would
 * show the clinician a debt they have just collected.
 *
 * A decline resolves with a `failed` row rather than rejecting, exactly as
 * `useCreateCharge` does, so callers read `data.status`.
 */
export function useChargeBalance(token?: string) {
  return useAuthMutation<ChargeResponse, { patientId: string }>({
    mutationFn: ({ patientId }) => chargeBalance(patientId, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.payments.charges(patientId),
      queryKeys.payments.balance(patientId),
      queryKeys.billing.balances(),
    ],
  })
}

/**
 * Write off part or all of a client's balance.
 *
 * Invalidates the ledger and the balance, exactly like a charge does — a
 * write-off changes the same figures a payment would, just in the other
 * direction.
 */
export function useCreateWriteOff(token?: string) {
  return useAuthMutation<
    ChargeResponse,
    { patientId: string; data: CreateWriteOffRequest }
  >({
    mutationFn: ({ patientId, data }) => createWriteOff(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.payments.charges(patientId),
      queryKeys.payments.balance(patientId),
      queryKeys.billing.balances(),
    ],
  })
}
