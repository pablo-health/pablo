// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Scheduling API Functions
 *
 * Type-safe wrappers for appointment scheduling endpoints.
 */

import type {
  AppointmentListResponse,
  AppointmentResponse,
  CreateAppointmentRequest,
  CreateRecurringAppointmentRequest,
  EditSeriesRequest,
  UpdateAppointmentRequest,
} from "@/types/scheduling"
import { apiClient, del, get, patch, post } from "./client"

// --- iCal sync types ---

export interface ICalSyncResponse {
  created: number
  updated: number
  deleted: number
  unchanged: number
  unmatched_events: UnmatchedEvent[]
  errors: string[]
}

export interface UnmatchedEvent {
  ical_uid: string
  client_identifier: string
  start_at: string
  ehr_appointment_url: string
}

export interface ICalConnectionStatus {
  ehr_system: string
  connected: boolean
  last_synced_at: string | null
  last_sync_error: string | null
}

export interface ICalStatusResponse {
  connections: ICalConnectionStatus[]
}

export interface ICalConfigureResponse {
  message: string
  event_count: number
  ehr_system: string
}

export interface ImportClientsResponse {
  imported: number
  updated: number
  skipped: number
  mappings_created: number
  errors: string[]
}

/**
 * Create a new appointment.
 */
export async function createAppointment(
  data: CreateAppointmentRequest,
  token?: string
): Promise<AppointmentResponse> {
  return post<AppointmentResponse>("/api/appointments", data, token)
}

/**
 * Create a recurring appointment series. Returns every materialized
 * occurrence created for the series.
 */
export async function createRecurringAppointment(
  data: CreateRecurringAppointmentRequest,
  token?: string
): Promise<AppointmentListResponse> {
  return post<AppointmentListResponse>("/api/appointments/recurring", data, token)
}

/**
 * List appointments in a date range.
 */
export async function listAppointments(
  start: string,
  end: string,
  token?: string
): Promise<AppointmentListResponse> {
  const params = new URLSearchParams({ start, end })
  return get<AppointmentListResponse>(`/api/appointments?${params}`, token)
}

/**
 * Get a single appointment by ID.
 */
export async function getAppointment(
  appointmentId: string,
  token?: string
): Promise<AppointmentResponse> {
  return get<AppointmentResponse>(`/api/appointments/${appointmentId}`, token)
}

/**
 * Update an appointment.
 */
export async function updateAppointment(
  appointmentId: string,
  data: UpdateAppointmentRequest,
  token?: string
): Promise<AppointmentResponse> {
  return patch<AppointmentResponse>(`/api/appointments/${appointmentId}`, data, token)
}

/**
 * Cancel an appointment (soft delete).
 */
export async function cancelAppointment(
  appointmentId: string,
  token?: string
): Promise<AppointmentResponse> {
  return del<AppointmentResponse>(`/api/appointments/${appointmentId}`, token)
}

/**
 * Edit all future occurrences of a recurring series, starting from the
 * given appointment. Returns every occurrence that was updated.
 */
export async function editAppointmentSeries(
  appointmentId: string,
  data: EditSeriesRequest,
  token?: string
): Promise<AppointmentListResponse> {
  return post<AppointmentListResponse>(
    `/api/appointments/${appointmentId}/edit-series`,
    data,
    token
  )
}

/**
 * Cancel all future occurrences of a recurring series, starting from the
 * given appointment. Returns every occurrence that was cancelled.
 */
export async function cancelAppointmentSeries(
  appointmentId: string,
  token?: string
): Promise<AppointmentListResponse> {
  return del<AppointmentListResponse>(
    `/api/appointments/${appointmentId}/cancel-series`,
    token
  )
}

// --- iCal sync API ---

export async function configureICalSync(
  ehr_system: string,
  feed_url: string
): Promise<ICalConfigureResponse> {
  return post<ICalConfigureResponse>("/api/ical-sync/configure", {
    ehr_system,
    feed_url,
  })
}

export async function triggerICalSync(
  ehr_system?: string
): Promise<ICalSyncResponse[]> {
  const params = ehr_system
    ? `?ehr_system=${encodeURIComponent(ehr_system)}`
    : ""
  return post<ICalSyncResponse[]>(`/api/ical-sync/sync${params}`, {})
}

export async function getICalSyncStatus(): Promise<ICalStatusResponse> {
  return get<ICalStatusResponse>("/api/ical-sync/status")
}

export async function disconnectICalSync(
  ehr_system: string
): Promise<{ message: string }> {
  return del<{ message: string }>(`/api/ical-sync/${ehr_system}`)
}

export async function resolveICalClient(
  ehr_system: string,
  client_identifier: string,
  patient_id: string
): Promise<{ message: string }> {
  return post<{ message: string }>("/api/ical-sync/resolve-client", {
    ehr_system,
    client_identifier,
    patient_id,
  })
}

export async function importClients(
  ehr_system: string,
  file: File
): Promise<ImportClientsResponse> {
  const formData = new FormData()
  formData.append("file", file)
  return apiClient<ImportClientsResponse>(
    `/api/ical-sync/import-clients?ehr_system=${encodeURIComponent(ehr_system)}`,
    {
      method: "POST",
      body: formData,
      headers: {}, // Let browser set Content-Type with boundary
    }
  )
}

// --- Google Calendar connect ---
//
// The wizard speaks in choices, never in Google permission names: the
// backend turns a choice into the narrowest grant that satisfies it, and
// hands back the promise that choice can honestly make.

/** Which calendar Pablo writes sessions to. */
export type CalendarWriteTarget = "app_calendar" | "primary"

export interface GoogleCalendarSelection {
  write_target: CalendarWriteTarget
  /** Whether to also ask for the therapist's busy times. */
  busy: boolean
}

export interface GoogleCalendarConsentOption {
  id: string
  /** What the underlying grant is limited to, and who holds that limit. */
  promise: string
}

export interface GoogleCalendarConsentOptions {
  write_targets: GoogleCalendarConsentOption[]
  busy: GoogleCalendarConsentOption
  default_write_target: CalendarWriteTarget
  busy_default: boolean
}

export interface GoogleCalendarStatus {
  connected: boolean
  calendar_id: string | null
  last_synced_at: string | null
  write_target: CalendarWriteTarget | null
}

function selectionParams(selection: GoogleCalendarSelection): string {
  return `write_target=${encodeURIComponent(selection.write_target)}&busy=${selection.busy}`
}

export async function getGoogleCalendarConsentOptions(): Promise<GoogleCalendarConsentOptions> {
  return get<GoogleCalendarConsentOptions>("/api/google-calendar/consent-options")
}

export async function getGoogleCalendarAuthUrl(
  redirectUri: string,
  selection: GoogleCalendarSelection
): Promise<{ auth_url: string }> {
  return get<{ auth_url: string }>(
    `/api/google-calendar/authorize?redirect_uri=${encodeURIComponent(redirectUri)}&${selectionParams(selection)}`
  )
}

/** `state` is the value Google handed back with the code. The backend
 * requires it and checks it was minted for the signed-in user, so the
 * callback cannot be driven with a code obtained anywhere else. */
export async function completeGoogleCalendarConnect(
  code: string,
  state: string,
  redirectUri: string,
  selection: GoogleCalendarSelection
): Promise<{ status: string }> {
  return get<{ status: string }>(
    `/api/google-calendar/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}&redirect_uri=${encodeURIComponent(redirectUri)}&${selectionParams(selection)}`
  )
}

export async function getGoogleCalendarStatus(): Promise<GoogleCalendarStatus> {
  return get<GoogleCalendarStatus>("/api/google-calendar/status")
}

export async function disconnectGoogleCalendar(): Promise<{ status: string }> {
  return del<{ status: string }>("/api/google-calendar/disconnect")
}
