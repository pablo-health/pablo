// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Appointment type API functions.
 *
 * Type-safe wrappers for the practice's appointment-type catalog — one
 * scheduling window per kind of appointment. See
 * backend/app/routes/scheduling.py's "Appointment type endpoints" section.
 */

import type {
  AppointmentTypeListResponse,
  AppointmentTypeResponse,
  CreateAppointmentTypeRequest,
  UpdateAppointmentTypeRequest,
} from "@/types/scheduling"
import { del, get, patch, post } from "./client"

const ENDPOINT = "/api/appointment-types"

export async function listAppointmentTypes(token?: string): Promise<AppointmentTypeListResponse> {
  return get<AppointmentTypeListResponse>(ENDPOINT, token)
}

export async function createAppointmentType(
  data: CreateAppointmentTypeRequest,
  token?: string
): Promise<AppointmentTypeResponse> {
  return post<AppointmentTypeResponse>(ENDPOINT, data, token)
}

export async function updateAppointmentType(
  appointmentTypeId: string,
  data: UpdateAppointmentTypeRequest,
  token?: string
): Promise<AppointmentTypeResponse> {
  return patch<AppointmentTypeResponse>(`${ENDPOINT}/${appointmentTypeId}`, data, token)
}

export async function deleteAppointmentType(
  appointmentTypeId: string,
  token?: string
): Promise<void> {
  return del<void>(`${ENDPOINT}/${appointmentTypeId}`, token)
}
