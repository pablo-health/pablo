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
  BalanceResponse,
  CardOnFileResponse,
  CardSetupResponse,
  ChargeAmountResponse,
  ChargeResponse,
  CreateChargeRequest,
} from "@/types/payments"
import { ApiError, buildApiUrl, get, getAuthHeader, post } from "./client"

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

/**
 * What the client owes, totalled from the ledger on the server.
 *
 * Never computed on this side. The rules about which rows are a bill and
 * which are a payment live in one module on the backend, and a second copy
 * here would be a second answer to "what do I owe" — the one question a
 * practice cannot afford two answers to.
 *
 * Unlike the routes above it, this one is not gated on the card processor: a
 * practice that only bills insurance still has clients who owe it money.
 */
export async function fetchPatientBalance(
  patientId: string,
  token?: string,
): Promise<BalanceResponse> {
  return get<BalanceResponse>(`/api/patients/${patientId}/balance`, token)
}

/**
 * Charge the card on file for the whole balance.
 *
 * No amount: the server reads it from the ledger at the moment of charging,
 * so a figure this browser saw before a remittance landed cannot be the one
 * that gets charged. A decline resolves with a `failed` row, as everywhere
 * else on this path; a client who owes nothing is a 409.
 */
export async function chargeBalance(
  patientId: string,
  token?: string,
): Promise<ChargeResponse> {
  return post<ChargeResponse>(`/api/patients/${patientId}/charge-balance`, {}, token)
}

/** The client's statement, as a PDF blob. */
export async function fetchStatement(patientId: string, token?: string): Promise<Blob> {
  const response = await fetch(buildApiUrl(`/api/patients/${patientId}/statement`), {
    method: "GET",
    headers: await getAuthHeader(token),
  })
  if (!response.ok) {
    throw new ApiError(
      "UNKNOWN_ERROR",
      `API request failed with status ${response.status}`,
      undefined,
      response.status,
    )
  }
  return response.blob()
}

/** The filename the route sends, so the download is named the same way. */
export const STATEMENT_FILENAME = "statement.pdf"
