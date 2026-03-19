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
import { del, get, patch, post } from "./client"

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
