// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Medications API client
 *
 * Type-safe wrappers around the medication endpoints:
 * `GET /api/patients/{patient_id}/medications` — list with optional status filter,
 * `POST /api/patients/{patient_id}/medications` — create,
 * `PATCH /api/patients/{patient_id}/medications/{id}` — update,
 * `DELETE /api/patients/{patient_id}/medications/{id}` — remove.
 */

import type {
  CreateMedicationRequest,
  Medication,
  MedicationListResponse,
  MedicationStatus,
  UpdateMedicationRequest,
} from "@/types/medications"
import { del, get, patch, post } from "./client"

export async function listMedications(
  patientId: string,
  status?: MedicationStatus,
  token?: string,
): Promise<MedicationListResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ""
  return get<MedicationListResponse>(
    `/api/patients/${patientId}/medications${query}`,
    token,
  )
}

export async function createMedication(
  patientId: string,
  data: CreateMedicationRequest,
  token?: string,
): Promise<Medication> {
  return post<Medication>(`/api/patients/${patientId}/medications`, data, token)
}

export async function updateMedication(
  patientId: string,
  medicationId: string,
  data: UpdateMedicationRequest,
  token?: string,
): Promise<Medication> {
  return patch<Medication>(
    `/api/patients/${patientId}/medications/${medicationId}`,
    data,
    token,
  )
}

export async function deleteMedication(
  patientId: string,
  medicationId: string,
  token?: string,
): Promise<void> {
  return del<void>(
    `/api/patients/${patientId}/medications/${medicationId}`,
    token,
  )
}
