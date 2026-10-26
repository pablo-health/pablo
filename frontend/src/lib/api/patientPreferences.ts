// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient preferences API client
 *
 * `GET  /api/patients/{patient_id}/preferences` — active preferences,
 * `POST /api/patients/{patient_id}/preferences` — add one (409 if the kind allows one and there is one),
 * `PATCH /api/patients/{patient_id}/preferences/{id}` — restate it,
 * `POST /api/patients/{patient_id}/preferences/{id}/retire` — retire it (the row is kept).
 */

import type {
  AddPreferenceRequest,
  Preference,
  PreferenceListResponse,
  UpdatePreferenceRequest,
} from "@/types/patientPreferences"
import { get, patch, post } from "./client"

export async function listPreferences(
  patientId: string,
  token?: string,
): Promise<PreferenceListResponse> {
  return get<PreferenceListResponse>(`/api/patients/${patientId}/preferences`, token)
}

export async function addPreference(
  patientId: string,
  data: AddPreferenceRequest,
  token?: string,
): Promise<Preference> {
  return post<Preference>(`/api/patients/${patientId}/preferences`, data, token)
}

export async function updatePreference(
  patientId: string,
  preferenceId: string,
  data: UpdatePreferenceRequest,
  token?: string,
): Promise<Preference> {
  return patch<Preference>(`/api/patients/${patientId}/preferences/${preferenceId}`, data, token)
}

export async function retirePreference(
  patientId: string,
  preferenceId: string,
  token?: string,
): Promise<Preference> {
  return post<Preference>(
    `/api/patients/${patientId}/preferences/${preferenceId}/retire`,
    {},
    token,
  )
}
