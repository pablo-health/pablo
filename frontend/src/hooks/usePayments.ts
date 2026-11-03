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
  RecordPaymentRequest,
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
  recordPayment,
  startCardSetup,
} from "@/lib/api/payments"
import { queryKeys } from "@/lib/api/queryKeys"
import { getUserStatus } from "@/lib/api/users"
import { useAuthMutation, useAuthQuery } from "./useAuthQuery"
import { USER_STATUS_QUERY_KEY } from "./useProfessionalInfo"

/**
 * Whether this practice takes card payments: `undefined` while that is still
 * being read, then `true` or `false`.
 *
 * Read from the user-status response the dashboard and chart already load, so
 * it costs no request of its own. Every card read below waits on it, and the
 * card UI renders nothing until it is `true`.
 */
export function useCardPaymentsConfigured(): boolean | undefined {
  const { data } = useAuthQuery({
    queryKey: USER_STATUS_QUERY_KEY,
    queryFn: () => getUserStatus(),
    staleTime: 5 * 60 * 1000,
  })
  return data === undefined ? undefined : data.card_payments_configured === true
}

/**
 * The card on file for a client, or `null` when there is none.
 *
 * Not sent at all for a practice that takes no cards. Not retried: "no card"
 * is already a `null`, not an error.
 */
export function usePatientCard(patientId: string | undefined, token?: string) {
  const takesCards = useCardPaymentsConfigured()
  return useAuthQuery<CardOnFileResponse | null>({
    queryKey: queryKeys.payments.card(patientId ?? ""),
    queryFn: () => fetchCardOnFile(patientId!, token),
    enabled: !!patientId && takesCards === true,
    retry: false,
  })
}

/**
 * What a charge would come to, so the clinician sees the figure before
 * authorising it. `amount_cents` is `null` when no rate is set anywhere.
 * Not sent at all for a practice that takes no cards.
 */
export function useChargeAmount(
  patientId: string | undefined,
  appointmentId?: string,
  token?: string,
) {
  const takesCards = useCardPaymentsConfigured()
  return useAuthQuery<ChargeAmountResponse>({
    queryKey: queryKeys.payments.amount(patientId ?? "", appointmentId),
    queryFn: () => fetchChargeAmount(patientId!, appointmentId, token),
    enabled: !!patientId && takesCards === true,
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
 * What the client owes, totalled on the server from their ledger. Read
 * whether or not the practice takes cards: a practice that only bills
 * insurance still has clients who owe it money.
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

/**
 * Record money the practice already took.
 *
 * Invalidates the same three keys a charge does. The money moved before this
 * call rather than because of it, but the figures it changes are the same
 * ones — and the balance is the whole reason the route exists.
 */
export function useRecordPayment(token?: string) {
  return useAuthMutation<ChargeResponse, { patientId: string; data: RecordPaymentRequest }>({
    mutationFn: ({ patientId, data }) => recordPayment(patientId, data, token),
    invalidateKeys: ({ patientId }) => [
      queryKeys.payments.charges(patientId),
      queryKeys.payments.balance(patientId),
      queryKeys.billing.balances(),
    ],
  })
}
