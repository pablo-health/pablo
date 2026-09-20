// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient-side client for a person's own appointments: what they have, what
 * their practice lets them do about it, and the three verbs that do it.
 *
 * Deliberately a bare `fetch` rather than the clinician API client, for the
 * same reason the intake and messaging clients are. That client resolves its
 * credential from the signed-in clinician's auth provider, and nobody on this
 * surface holds one — a patient has a short-lived portal session token, which
 * every function here takes as its first argument and sends as
 * `Authorization: Bearer …`.
 *
 * **No function here takes a patient id.** The routes derive it from the
 * token, so there is no field for a caller to put the wrong value in, and a
 * forged request cannot book against somebody else.
 *
 * **Results are returned, not thrown.** Every call answers
 * {@link PatientApiResult}, because on this surface the interesting failures
 * are ordinary answers rather than faults: 403 means the practice does not
 * offer this, and 409 means the time went or the change is late and needs
 * confirming. Each of those is a screen, so the caller gets the status and the
 * code rather than an exception to pattern-match on.
 */

import { buildApiUrl } from "@/lib/api/client"

/** Mirrors the engine's `AppointmentStatus`. */
export type AppointmentStatus =
  | "pending"
  | "confirmed"
  | "cancelled"
  | "no_show"
  | "completed"

/**
 * One appointment, as its own patient may see it.
 *
 * Mirrors the engine's `PatientAppointmentResponse`, which is the only shape
 * this surface renders. That model is the projection standing between a
 * patient and the rest of the appointments row — the clinician's notes, the
 * visit-coding fields, the calendar-sync internals — so widening this
 * interface with a field the server does not send is not merely useless, it
 * invites a later route to start sending it.
 */
export interface PatientAppointment {
  id: string
  start_at: string
  end_at: string
  duration_minutes: number
  status: AppointmentStatus
  session_type: string
  video_link?: string | null
  video_platform?: string | null
  /**
   * Which video service the appointment is held on, when it is held on one.
   *
   * Sent whether or not there is a link yet, which is what lets the list say
   * a link is coming rather than showing a row with nothing on it.
   */
  provider?: string | null
  recurrence_rule?: string | null
  recurring_appointment_id?: string | null
  /**
   * Whether this was cancelled with less notice than the practice asks for,
   * so its policy may apply. `null` on anything not cancelled.
   */
  late_cancellation?: boolean | null
}

/**
 * One bookable opening.
 *
 * Times only, and that is the security property rather than an omission:
 * openings are computed from the whole diary, so a slot carrying anything
 * about the appointments that shaped it would be describing other patients.
 */
export interface PatientSlot {
  start_at: string
  end_at: string
  duration_minutes: number
}

/** A kind of appointment the practice lets its patients book. */
export interface PatientBookableType {
  name: string
  duration_minutes: number
}

/**
 * What this practice lets a patient do with their own appointments.
 *
 * Answered whether or not booking is on, which is why the portal can render
 * the right thing the first time instead of drawing a control and withdrawing
 * it. `self_booking` false is a complete answer, and `practice_phone` is how
 * a patient acts on it.
 */
export interface PatientBookingOptions {
  self_booking: boolean
  session_types: PatientBookableType[]
  min_notice_hours: number
  max_horizon_days: number
  cancel_cutoff_hours: number
  reschedule_cutoff_hours: number
  /**
   * The IANA zone the practice keeps its diary in. Every time this module
   * renders is formatted in it, so a patient reading from another timezone
   * sees the hour their practice means rather than the one their laptop is
   * set to.
   */
  practice_timezone: string
  /**
   * How long before the start a join link is offered, in minutes.
   *
   * Read from the server rather than held here, so the patient's screen and
   * the clinician's diary cannot come to different conclusions about whether
   * an appointment can be joined yet.
   */
  join_window_before_minutes: number
  practice_phone: string | null
}

/**
 * A failed call, as this surface treats failure: the status, and the
 * engine's own error code and sentence when it sent one.
 *
 * `message` is the server's wording, not a translation of it. The late-change
 * refusal in particular is written to be shown to the patient — it is the
 * only thing standing between them and a fee nobody mentioned — so a client
 * that substituted its own words would be the weak point.
 */
export interface PatientApiFailure {
  ok: false
  status: number
  code: string | null
  message: string | null
}

export type PatientApiResult<T> = { ok: true; data: T } | PatientApiFailure

/** The engine writes its error envelope flat or under `detail`; read both. */
function envelopeFrom(body: unknown): { code?: string; message?: string } | null {
  if (typeof body !== "object" || body === null) return null
  const top = body as Record<string, unknown>
  const nested = top.detail
  const holder =
    typeof nested === "object" && nested !== null && "error" in nested
      ? (nested as Record<string, unknown>)
      : top
  const error = holder.error
  if (typeof error !== "object" || error === null) return null
  return error as { code?: string; message?: string }
}

async function failureFrom(response: Response): Promise<PatientApiFailure> {
  let envelope: { code?: string; message?: string } | null = null
  try {
    envelope = envelopeFrom(await response.json())
  } catch {
    // A non-JSON body is an ordinary outcome for a proxy error or a 502.
    // The status still tells the caller what to render.
  }
  return {
    ok: false,
    status: response.status,
    code: envelope?.code ?? null,
    message: envelope?.message ?? null,
  }
}

async function patientFetch<T>(
  path: string,
  args: { token: string; method?: string; body?: unknown },
): Promise<PatientApiResult<T>> {
  const response = await fetch(buildApiUrl(path), {
    method: args.method ?? "GET",
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${args.token}`,
      ...(args.body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: args.body !== undefined ? JSON.stringify(args.body) : undefined,
  })
  if (!response.ok) return failureFrom(response)
  return { ok: true, data: (await response.json()) as T }
}

/** What this practice lets the calling patient do. Answered even when off. */
export async function getBookingOptions(
  token: string,
): Promise<PatientApiResult<PatientBookingOptions>> {
  return patientFetch<PatientBookingOptions>("/api/patient/booking/options", { token })
}

/** The patient's own appointments, soonest first. */
export async function getPatientAppointments(
  token: string,
): Promise<PatientApiResult<PatientAppointment[]>> {
  const result = await patientFetch<{ data: PatientAppointment[] }>(
    "/api/patient/appointments",
    { token },
  )
  return result.ok ? { ok: true, data: result.data.data } : result
}

/**
 * The openings a patient could book on one calendar date.
 *
 * The date is local to the practice, which is the frame the clinician's
 * working hours are written in — so this sends the day the patient is looking
 * at rather than an instant, and lets the server resolve the timezone.
 */
export async function getPatientSlots(args: {
  token: string
  date: string
  durationMinutes?: number
}): Promise<PatientApiResult<PatientSlot[]>> {
  const query = new URLSearchParams({ date: args.date })
  if (args.durationMinutes !== undefined) {
    query.set("duration_minutes", String(args.durationMinutes))
  }
  const result = await patientFetch<{ data: PatientSlot[] }>(
    `/api/patient/booking/slots?${query.toString()}`,
    { token: args.token },
  )
  return result.ok ? { ok: true, data: result.data.data } : result
}

/**
 * Book a time.
 *
 * The body carries a time and a kind of appointment, which is everything a
 * patient chooses. NEVER add a patient id: the server derives the patient
 * from the session token, and a field here would be one an attacker could
 * fill in.
 */
export async function bookPatientAppointment(args: {
  token: string
  startAt: string
  sessionType: string
  durationMinutes?: number
}): Promise<PatientApiResult<PatientAppointment>> {
  return patientFetch<PatientAppointment>("/api/patient/booking", {
    token: args.token,
    method: "POST",
    body: {
      start_at: args.startAt,
      session_type: args.sessionType,
      ...(args.durationMinutes !== undefined
        ? { duration_minutes: args.durationMinutes }
        : {}),
    },
  })
}

/**
 * Move an appointment to a different time.
 *
 * `acknowledgeLateChange` is how a patient answers the engine's late-change
 * refusal, not something to send by default. The first attempt goes without
 * it; a change inside the practice's notice period comes back 409
 * `LATE_CHANGE_NOT_ACKNOWLEDGED` with the sentence to show, and the same
 * request carrying the flag then succeeds. Sending it up front would skip the
 * warning, which is the one thing the flag exists to prove was given.
 */
export async function reschedulePatientAppointment(args: {
  token: string
  appointmentId: string
  startAt: string
  acknowledgeLateChange?: boolean
}): Promise<PatientApiResult<PatientAppointment>> {
  return patientFetch<PatientAppointment>(
    `/api/patient/booking/${encodeURIComponent(args.appointmentId)}/reschedule`,
    {
      token: args.token,
      method: "POST",
      body: {
        start_at: args.startAt,
        acknowledge_late_change: args.acknowledgeLateChange ?? false,
      },
    },
  )
}

/** Cancel an appointment. Same two-step acknowledgement as rescheduling. */
export async function cancelPatientAppointment(args: {
  token: string
  appointmentId: string
  acknowledgeLateChange?: boolean
}): Promise<PatientApiResult<PatientAppointment>> {
  return patientFetch<PatientAppointment>(
    `/api/patient/booking/${encodeURIComponent(args.appointmentId)}/cancel`,
    {
      token: args.token,
      method: "POST",
      body: { acknowledge_late_change: args.acknowledgeLateChange ?? false },
    },
  )
}
