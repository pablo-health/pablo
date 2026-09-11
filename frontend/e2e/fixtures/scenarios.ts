// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Given X" helpers: put the practice into a known state through the API,
 * never through the UI. A spec drives the browser only for the behaviour
 * it is proving; everything before that is one of these.
 */

import type { ApiClient } from "./api"

let sequence = 0
const next = (): string => `${Date.now().toString(36)}${(sequence++).toString(36)}`

/** How many billable visits this worker has seeded; each gets its own hour. */
let visitsSeeded = 0

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
  /** X12 DMG03 administrative sex: M, F or U. On the claim's subscriber loop. */
  sex?: "M" | "F" | "U"
  address_line1?: string | null
  city?: string | null
  state?: string | null
  postal_code?: string | null
}

export async function givePatient(api: ApiClient, seed: PatientSeed = {}): Promise<Patient> {
  return api.post<Patient>("/api/patients", {
    first_name: `Given-${next()}`,
    last_name: "Patient",
    status: "active",
    ...seed,
  })
}

/**
 * A client a claim can actually be filed for.
 *
 * A payer will not adjudicate without knowing who the subscriber is, and
 * when the client is on their own plan the subscriber *is* the client — the
 * claim copies the patient's own demographics into the subscriber loop. So
 * a date of birth, a sex and an address are not chart decoration here; every
 * one of them is a blocking scrub finding when it is missing.
 */
export async function giveInsurablePatient(
  api: ApiClient,
  seed: PatientSeed = {},
): Promise<Patient> {
  return givePatient(api, {
    date_of_birth: "1985-03-14",
    sex: "F",
    address_line1: "88 Peachtree St",
    city: "Atlanta",
    state: "GA",
    postal_code: "30303",
    ...seed,
  })
}

export interface AvailabilityRule {
  id: string
  rule_type: string
  params: Record<string, unknown>
}

export async function giveAvailabilityRule(
  api: ApiClient,
  ruleType: string,
  params: Record<string, unknown>,
): Promise<AvailabilityRule> {
  return api.post<AvailabilityRule>("/api/availability/rules", {
    rule_type: ruleType,
    enforcement: "hard",
    params,
  })
}

export interface ScheduledSession {
  id: string
  status: string
}

export async function giveScheduledSession(
  api: ApiClient,
  patientId: string,
  noteType?: string,
): Promise<ScheduledSession> {
  return api.post<ScheduledSession>("/api/sessions/schedule", {
    patient_id: patientId,
    scheduled_at: new Date().toISOString(),
    source: "companion",
    ...(noteType === undefined ? {} : { note_type: noteType }),
  })
}

export async function markCalendarSetupComplete(api: ApiClient): Promise<void> {
  const preferences = await api.get<Record<string, unknown>>("/api/users/me/preferences")
  if (preferences.calendar_setup_complete !== true) {
    await api.put("/api/users/me/preferences", { ...preferences, calendar_setup_complete: true })
  }
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

export interface BilledVisit {
  appointmentId: string
  sessionId: string
  noteId: string
}

/**
 * A visit that has happened, is coded, and is waiting to be billed.
 *
 * Three things have to be true before Billing offers to file a claim, and
 * only one of them is obvious. The visit needs its codes; it needs a
 * *session*, linked to the appointment — a link only
 * `POST /api/appointments/{id}/start-session` makes, never an appointment
 * PATCH; and that session's note has to be **finalized**, because "unbilled"
 * is derived from `finalized_at` rather than stored, so a draft note leaves
 * the visit invisible to the queue rather than merely unbilled.
 */
export async function giveVisitReadyToBill(
  api: ApiClient,
  patientId: string,
  codes: VisitCodes,
): Promise<BilledVisit> {
  // Each visit is booked an hour before the last. The calendar refuses two
  // appointments that overlap, so a fixed time would let one spec seed a
  // visit and the next fail on a 409 that says nothing about why.
  visitsSeeded += 1
  const appointment = await giveSessionWithCodes(api, patientId, codes, hoursAgo(visitsSeeded + 1))
  const session = await api.post<{ id: string }>(
    `/api/appointments/${appointment.id}/start-session`,
    {},
  )
  const detail = await api.get<{ note: { id: string } | null }>(`/api/sessions/${session.id}`)
  const noteId = detail.note?.id
  if (!noteId) {
    throw new Error(`started session ${session.id} has no note to finalize`)
  }
  await api.patch(`/api/notes/${noteId}`, {
    content_edited: {
      subjective: "Reports steady mood since the last visit.",
      objective: "Alert, oriented, no acute distress.",
      assessment: "Adjustment-related anxiety, improving.",
      plan: "Continue weekly sessions; practise paced breathing.",
    },
  })
  await api.post(`/api/notes/${noteId}/finalize`, {})
  return { appointmentId: appointment.id, sessionId: session.id, noteId }
}

export interface AvailabilityRule {
  id: string
  rule_type: string
  params: Record<string, unknown>
}

/**
 * Working hours on one weekday (0 = Monday, matching the engine's
 * `date.weekday()`), which is what makes free slots exist at all: a
 * clinician with no rules reads as "availability not set up" rather than
 * "no openings".
 *
 * Rules accumulate, and two identical ones would produce every slot twice,
 * so a spec that seeds several should give each its own weekday.
 */
export async function giveWorkingHours(
  api: ApiClient,
  dayOfWeek: number,
  hours: { start?: string; end?: string } = {},
): Promise<AvailabilityRule> {
  return api.post<AvailabilityRule>("/api/availability/rules", {
    rule_type: "working_hours",
    params: { day_of_week: dayOfWeek, start: hours.start ?? "09:00", end: hours.end ?? "17:00" },
  })
}

export interface AppointmentType {
  id: string
  name: string
  duration_minutes: number
  audience: "new" | "existing" | "both"
  self_bookable: boolean
  offerable: boolean
}

export interface AppointmentTypeSeed {
  name?: string
  duration_minutes?: number
  audience?: AppointmentType["audience"]
  self_bookable?: boolean
  offerable?: boolean
}

/**
 * An appointment type a stranger may book: for new clients, self-bookable,
 * offered. Every switch defaults to the open position here because this is
 * the fixture for "a link that works"; pass a closed switch to test a gate.
 */
export async function giveBookableType(
  api: ApiClient,
  seed: AppointmentTypeSeed = {},
): Promise<AppointmentType> {
  return api.post<AppointmentType>("/api/appointment-types", {
    name: `Intake call ${next()}`,
    duration_minutes: 50,
    audience: "new",
    self_bookable: true,
    offerable: true,
    ...seed,
  })
}

/**
 * The practice-wide switch. Off by default for every practice, so a spec
 * that books through a link must turn it on first, and a spec that wants
 * the closed state passes `false`.
 */
export async function letNewClientsSelfBook(api: ApiClient, allowed = true): Promise<void> {
  await api.patch("/api/scheduling/policy", { self_book_new: allowed })
}

export interface BookingLink {
  id: string
  slug: string
  host_name: string
  title: string
  appointment_type_id: string
  appointment_type_name: string | null
  duration_minutes: number | null
  bookable: boolean
  not_bookable_reason: string | null
  is_active: boolean
}

/**
 * A booking link that a stranger can book through, end to end: its own
 * open appointment type and the practice switch on. Pass `appointment_type_id`
 * to point it at a type you built yourself.
 */
export async function giveBookingLink(
  api: ApiClient,
  seed: Partial<{ slug: string; host_name: string; title: string; appointment_type_id: string }> = {},
): Promise<BookingLink> {
  const appointment_type_id = seed.appointment_type_id ?? (await giveBookableType(api)).id
  await letNewClientsSelfBook(api)
  return api.post<BookingLink>("/api/booking-links", {
    slug: `e2e-${next()}`,
    host_name: "E2E Clinician",
    title: "Intake call",
    ...seed,
    appointment_type_id,
  })
}

export interface CoverageSeed {
  /** The electronic payer id. Defaults to the fake clearinghouse's test payer. */
  payer_id?: string
  payer_name?: string
  member_id?: string
}

export interface Coverage {
  id: string
  patient_id: string
  member_id: string
}

/**
 * A plan on file for a patient, with the payer added on the way through.
 *
 * The coverage API takes either a payer already on the practice's list or a
 * new one typed from the card; this uses the second, so a spec needs no
 * separate payer step and two specs cannot collide over one payer row.
 */
export async function giveCoverage(
  api: ApiClient,
  patientId: string,
  seed: CoverageSeed = {},
): Promise<Coverage> {
  return api.post<Coverage>(`/api/patients/${patientId}/coverage`, {
    new_payer: {
      name: seed.payer_name ?? "Stedi Test Payer",
      payer_id: seed.payer_id ?? "STEDI",
    },
    member_id: seed.member_id ?? `MEM${next().toUpperCase()}`,
    subscriber_relationship: "self",
  })
}

/**
 * The practice's own billing identity, complete enough to file a claim.
 *
 * Three separate records, and a claim needs all three: the practice that
 * bills (legal name, tax id, billing NPI, address), the clinician who
 * rendered the service (their name and their own type-1 NPI, which is a
 * different NPI from the practice's), and the taxonomy code some payers deny
 * without. Missing any of them is a blocking scrub finding rather than a
 * rejection weeks later, which is the point of the scrub.
 */
export async function givePracticeReadyToBill(api: ApiClient): Promise<void> {
  // Two words: the claim splits a display name into first and last, and a
  // single word leaves the rendering provider with no surname.
  await api.patch("/api/users/me", { legal_name: "Jordan Reeves" })
  await api.patch("/api/users/me/professional-info", {
    npi_number: "1999999984",
    taxonomy_code: "101YM0800X",
  })
  await api.patch("/api/practice/billing-profile", {
    legal_name: "Pablo Health Test Provider",
    tax_id: "84-4459714",
    tax_id_type: "ein",
    billing_npi: "1999999984",
    address_line1: "1 Test St",
    city: "Atlanta",
    state: "GA",
    postal_code: "30301",
    phone: "4045550100",
    contact_email: "billing@example.com",
  })
}

/** A visit that has already happened, which is what Billing bills for. */
function hoursAgo(hours: number): Date {
  return new Date(Date.now() - hours * 60 * 60 * 1000)
}

function tomorrowAt(hourUtc: number): Date {
  const when = new Date()
  when.setUTCDate(when.getUTCDate() + 1)
  when.setUTCHours(hourUtc, 0, 0, 0)
  return when
}
