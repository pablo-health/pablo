// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-side client for asking the practice for a medication refill.
 *
 * Written to the same shape as `patientMessages.ts`, for the same reasons:
 * a bare `fetch` carrying the short-lived patient session token as
 * `Authorization: Bearer …`, because the clinician API client resolves its
 * credential from a signed-in clinician and the portal has none.
 *
 * Nothing here takes a patient id. The routes derive it from the token.
 *
 * Failures carry a status and nothing else. A medication name or a note to
 * a prescriber is health information, and an error that quoted one would
 * put it into console output and error reporting.
 */

import { buildApiUrl } from "@/lib/api/client"

const BASE = "/api/patient/refills"

/** Server-side limits, mirrored so the inputs stop where the route would reject. */
export const MEDICATION_TEXT_MAX = 200
export const PHARMACY_TEXT_MAX = 200
export const PATIENT_NOTE_MAX = 2000

/** One of the patient's active medications, as the picker lists it. */
export interface PatientRefillMedication {
  id: string
  drug_name: string
  dose: string
}

export interface PatientRefillMedicationList {
  data: PatientRefillMedication[]
  total: number
}

export type RefillRequestStatus = "requested" | "approved" | "needs_visit" | "declined"

export interface RefillRequest {
  id: string
  /** Set when the patient picked from their list; null when they typed a name. */
  medication_id: string | null
  /** What the list shows. Always a string, including for a request that named a listed medication. */
  medication_text: string
  pharmacy_text: string | null
  patient_note: string | null
  status: RefillRequestStatus
  created_at: string
  decided_at: string | null
}

export interface RefillRequestList {
  data: RefillRequest[]
  total: number
}

/**
 * What a new request carries. Exactly one of `medication_id` and
 * `medication_text` is set; the route answers 422 otherwise.
 */
export interface CreateRefillRequestInput {
  medication_id?: string | null
  medication_text?: string | null
  pharmacy_text?: string | null
  patient_note?: string | null
}

/** A failed request. Carries the status and no part of the payload. */
export class PatientRefillsError extends Error {
  constructor(public status: number) {
    super(`Patient refill request failed (${status})`)
    this.name = "PatientRefillsError"
  }
}

function headers(sessionToken: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${sessionToken}`,
  }
}

async function request<T>(
  sessionToken: string,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(buildApiUrl(`${BASE}${path}`), {
    ...init,
    headers: headers(sessionToken),
  })
  if (!response.ok) throw new PatientRefillsError(response.status)
  return (await response.json()) as T
}

/** The patient's active medications, for the picker. */
export async function listRefillMedications(
  sessionToken: string,
): Promise<PatientRefillMedicationList> {
  return request<PatientRefillMedicationList>(sessionToken, "/medications")
}

/** The patient's refill requests, newest first. */
export async function listRefillRequests(
  sessionToken: string,
): Promise<RefillRequestList> {
  return request<RefillRequestList>(sessionToken, "")
}

/** Ask for a refill. */
export async function createRefillRequest(
  sessionToken: string,
  input: CreateRefillRequestInput,
): Promise<RefillRequest> {
  return request<RefillRequest>(sessionToken, "", {
    method: "POST",
    body: JSON.stringify({
      medication_id: input.medication_id ?? null,
      medication_text: input.medication_text ?? null,
      pharmacy_text: input.pharmacy_text ?? null,
      patient_note: input.patient_note ?? null,
    }),
  })
}
