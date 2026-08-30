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

/** Complete an incremental capability grant — currently only "import",
 * asked for from the practice-import wizard rather than at connect. Reuses
 * the connect callback, but must never carry a write_target: an
 * incremental grant is read back from the existing connection server-side,
 * so it can't silently rebind PUSH to a different calendar mid-flow. */
export async function completeGoogleCalendarImportConsent(
  code: string,
  state: string,
  redirectUri: string
): Promise<{ status: string }> {
  return get<{ status: string }>(
    `/api/google-calendar/callback?code=${encodeURIComponent(code)}&state=${encodeURIComponent(state)}&redirect_uri=${encodeURIComponent(redirectUri)}&capability=import`
  )
}

// --- Calendar practice import (scan an existing calendar, propose, confirm) ---
//
// A scan reads and proposes; nothing is written until a confirmation names
// the subset to keep. `summary` is the calendar's own wording and belongs
// on screen for the therapist to read — never logged, matched, or sent
// anywhere else.

export interface ProposedSeries {
  candidate_key: string
  summary: string
  /** Monday is 0, matching Python's weekday(). */
  weekday: number
  /** HH:MM in the calendar's own timezone. */
  local_start_time: string
  duration_minutes: number
  cadence: "weekly" | "biweekly"
  occurrences_in_window: number
  /** Occurrences still to come — the importable ones. */
  occurrences_ahead: number
  first_future_start: string | null
  last_seen: string
  recurrence_rule: string
  status: "active" | "looks_finished"
  confidence: number
  preselected: boolean
}

export interface ImportProposal {
  series: ProposedSeries[]
  /** Events that matched nothing — a count, never their titles. */
  left_alone: number
  events_read: number
  partial: boolean
  lookback_days: number
  horizon_days: number
  timezone: string
}

export interface ImportConsentRequired {
  needs_consent: true
  capability: "import"
  auth_url: string
}

export function importNeedsConsent(
  result: ImportProposal | ImportConsentRequired
): result is ImportConsentRequired {
  return "needs_consent" in result && result.needs_consent === true
}

export async function scanCalendarForImport(
  redirectUri: string
): Promise<ImportProposal | ImportConsentRequired> {
  return post<ImportProposal | ImportConsentRequired>(
    `/api/calendar/import/scan?redirect_uri=${encodeURIComponent(redirectUri)}`,
    {}
  )
}

export interface ConfirmImportSeriesInput {
  candidate_key: string
  display_name: string
  /** First occurrence to create — must be in the future. */
  start_at: string
  duration_minutes: number
  cadence: string
  occurrences: number
  timezone: string
}

export interface ConfirmedSeries {
  candidate_key: string
  patient_id: string
  appointments_created: number
}

export interface ConfirmImportResult {
  confirmed: ConfirmedSeries[]
  patients_created: number
  appointments_created: number
  /** Candidate keys whose chart was created but whose recurring series
   * collided with something already booked. Keys only, never titles. */
  skipped: string[]
}

export async function confirmCalendarImport(
  series: ConfirmImportSeriesInput[]
): Promise<ConfirmImportResult> {
  return post<ConfirmImportResult>("/api/calendar/import/confirm", { series })
}

// --- Busy windows — the anonymous pre-scan week grid's data source ---

export interface BusyWindow {
  start: string
  end: string
}

export interface BusyWindowsGranted {
  windows: BusyWindow[]
}

export interface BusyWindowsNotGranted {
  granted: false
}

export function busyWindowsGranted(
  result: BusyWindowsGranted | BusyWindowsNotGranted
): result is BusyWindowsGranted {
  return "windows" in result
}

export async function getCalendarBusyWindows(
  start: string,
  end: string
): Promise<BusyWindowsGranted | BusyWindowsNotGranted> {
  const params = new URLSearchParams({ start, end })
  return get<BusyWindowsGranted | BusyWindowsNotGranted>(`/api/calendar/import/busy?${params}`)
}
