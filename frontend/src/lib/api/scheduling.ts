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
