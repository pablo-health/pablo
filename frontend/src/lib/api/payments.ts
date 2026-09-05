// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Self-pay card payments API client.
 *
 * Wraps `app.routes.patient_payments`. Nothing in this module handles a card
 * number — collecting one is a direct browser-to-Stripe exchange against the
 * SetupIntent `startCardSetup` returns, and all `completeCardSetup` sends back
 * is the id of the SetupIntent the browser confirmed.
 */

import type {
  CardOnFileResponse,
  CardSetupResponse,
  ChargeAmountResponse,
  ChargeResponse,
  CreateChargeRequest,
} from "@/types/payments"
import { ApiError, get, post } from "./client"

/**
 * True for the one failure that is a deployment fact rather than a fault: this
 * practice has no card processing configured, which every payment route
 * reports as a 503. Surfaces differently from a real error — there is nothing
 * for the clinician to retry.
 */
export function isPaymentsUnconfigured(error: unknown): boolean {
  return error instanceof ApiError && error.status === 503
}

export async function startCardSetup(
  patientId: string,
  token?: string,
): Promise<CardSetupResponse> {
  return post<CardSetupResponse>(
    `/api/patients/${patientId}/payment-method/setup`,
    {},
    token,
  )
}

export async function completeCardSetup(
  patientId: string,
  setupIntentId: string,
  token?: string,
): Promise<CardOnFileResponse> {
  return post<CardOnFileResponse>(
    `/api/patients/${patientId}/payment-method`,
    { setup_intent_id: setupIntentId },
    token,
  )
}

/**
 * The card on file, or `null` when there is none.
 *
 * The route answers "no card" with a 404, matching its unknown-client shape.
 * That is a normal answer here rather than a failure, so it is translated
 * once — otherwise every caller would have to tell an absent card apart from
 * a request that actually went wrong.
 */
export async function fetchCardOnFile(
  patientId: string,
  token?: string,
): Promise<CardOnFileResponse | null> {
  try {
    return await get<CardOnFileResponse>(
      `/api/patients/${patientId}/payment-method`,
      token,
    )
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null
    throw error
  }
}

export async function fetchChargeAmount(
  patientId: string,
  appointmentId?: string,
  token?: string,
): Promise<ChargeAmountResponse> {
  const query = appointmentId
    ? `?appointment_id=${encodeURIComponent(appointmentId)}`
    : ""
  return get<ChargeAmountResponse>(
    `/api/patients/${patientId}/charge-amount${query}`,
    token,
  )
}

/**
 * Charge the card on file. One call, one charge — a decline is never retried
 * here, because a retry is a fresh charge the clinician has to ask for.
 *
 * Resolves with a `failed` row on a decline: the attempt is a real ledger
 * entry, and the reason is on it.
 */
export async function createCharge(
  patientId: string,
  data: CreateChargeRequest,
  token?: string,
): Promise<ChargeResponse> {
  return post<ChargeResponse>(`/api/patients/${patientId}/charges`, data, token)
}

export async function listCharges(
  patientId: string,
  token?: string,
): Promise<ChargeResponse[]> {
  return get<ChargeResponse[]>(`/api/patients/${patientId}/charges`, token)
}
