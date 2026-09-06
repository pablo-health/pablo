// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Given X" helpers: put the practice into a known state through the API,
 * never through the UI. A spec drives the browser only for the behaviour
 * it is proving; everything before that is one of these.
 */

import type { ApiClient } from "./api"

let sequence = 0
const next = (): string => `${Date.now().toString(36)}${(sequence++).toString(36)}`

export interface Patient {
  id: string
  first_name: string
  last_name: string
  email: string | null
  status: string
}

export interface PatientSeed {
  first_name?: string
  last_name?: string
  email?: string | null
  phone?: string | null
  status?: "active" | "inactive" | "on_hold"
  date_of_birth?: string | null
  diagnosis?: string | null
  rate_cents?: number | null
}

export async function givePatient(api: ApiClient, seed: PatientSeed = {}): Promise<Patient> {
  return api.post<Patient>("/api/patients", {
    first_name: `Given-${next()}`,
    last_name: "Patient",
    status: "active",
    ...seed,
  })
}

export interface Appointment {
  id: string
  patient_id: string
  start_at: string
  end_at: string
  service_code: string | null
  diagnosis_codes: string[] | null
}

/** The billing codes a clinician records on a visit. */
export interface VisitCodes {
  service_code: string
  modifiers?: string[]
  unit_count?: number
  place_of_service?: "11" | "02" | "10"
  diagnosis_codes: string[]
}

/**
 * A scheduled session carrying visit codes: the appointment the claim
 * assembly reads. Defaults to tomorrow at 10:00 UTC, 50 minutes.
 */
export async function giveSessionWithCodes(
  api: ApiClient,
  patientId: string,
  codes: VisitCodes,
  startAt: Date = tomorrowAt(10),
): Promise<Appointment> {
  const endAt = new Date(startAt.getTime() + 50 * 60 * 1000)
  const created = await api.post<Appointment>("/api/appointments", {
    patient_id: patientId,
    title: "Session",
    start_at: startAt.toISOString(),
    end_at: endAt.toISOString(),
    duration_minutes: 50,
    session_type: "individual",
  })
  return api.patch<Appointment>(`/api/appointments/${created.id}`, {
    place_of_service: "11",
    unit_count: 1,
    ...codes,
  })
}

export interface BookingLink {
  id: string
  slug: string
  title: string
  is_active: boolean
}

export async function giveBookingLink(
  api: ApiClient,
  seed: Partial<{ slug: string; host_name: string; title: string; duration_minutes: number }> = {},
): Promise<BookingLink> {
  return api.post<BookingLink>("/api/booking-links", {
    slug: `e2e-${next()}`,
    host_name: "E2E Clinician",
    title: "Intake call",
    duration_minutes: 50,
    session_type: "individual",
    ...seed,
  })
}

export interface CoverageSeed {
  payer_id?: string
  member_id?: string
}

/**
 * Coverage on file for a patient. Lands with the coverage work; until then
 * the helper is a typed placeholder so a spec written against it fails
 * loudly rather than compiling against a guess at the API.
 */
export async function giveCoverage(
  _api: ApiClient,
  _patientId: string,
  _seed: CoverageSeed = {},
): Promise<never> {
  throw new Error("coverage API not present")
}

function tomorrowAt(hourUtc: number): Date {
  const when = new Date()
  when.setUTCDate(when.getUTCDate() + 1)
  when.setUTCHours(hourUtc, 0, 0, 0)
  return when
}
