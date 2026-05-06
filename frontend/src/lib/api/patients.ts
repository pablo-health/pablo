// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient API Functions
 *
 * Type-safe wrappers for patient management API endpoints.
 */

import type {
  CreatePatientRequest,
  DeletePatientResponse,
  PatientListParams,
  PatientListResponse,
  PatientResponse,
  UpdatePatientRequest,
} from "@/types/patients"
import { del, get, patch, post } from "./client"

/**
 * Create a new patient
 *
 * @param data - Patient information
 * @param token - Optional auth token for server-side calls
 * @returns Created patient with ID and metadata
 *
 * @example
 * const patient = await createPatient({
 *   first_name: "Jane",
 *   last_name: "Doe",
 *   date_of_birth: "1985-03-15",
 *   diagnosis: "Anxiety"
 * })
 */
export async function createPatient(
  data: CreatePatientRequest,
  token?: string
): Promise<PatientResponse> {
  return post<PatientResponse>("/api/patients", data, token)
}

/**
 * List all patients with optional search
 *
 * @param params - Optional search parameters
 * @param token - Optional auth token for server-side calls
 * @returns Paginated list of patients
 *
 * @example
 * // Get all patients
 * const patients = await listPatients()
 *
 * // Search by last name
 * const results = await listPatients({ search: "Smith", search_by: "last_name" })
 */
export async function listPatients(
  params?: PatientListParams,
  token?: string
): Promise<PatientListResponse> {
  const queryParams = new URLSearchParams()
  if (params?.search) {
    queryParams.append("search", params.search)
  }
  if (params?.search_by) {
    queryParams.append("search_by", params.search_by)
  }
  if (params?.include_deleted) {
    queryParams.append("include_deleted", params.include_deleted)
  }

  const endpoint = queryParams.toString()
    ? `/api/patients?${queryParams.toString()}`
    : "/api/patients"

  return get<PatientListResponse>(endpoint, token)
}

/**
 * Get a single patient by ID
 *
 * @param patientId - Patient ID
 * @param token - Optional auth token for server-side calls
 * @returns Patient details
 * @throws ApiError with code "NOT_FOUND" if patient doesn't exist or doesn't belong to user
 *
 * @example
 * const patient = await getPatient("123e4567-e89b-12d3-a456-426614174000")
 */
export async function getPatient(
  patientId: string,
  token?: string
): Promise<PatientResponse> {
  return get<PatientResponse>(`/api/patients/${patientId}`, token)
}

/**
 * Update a patient's information
 *
 * @param patientId - Patient ID
 * @param data - Fields to update (all optional)
 * @param token - Optional auth token for server-side calls
 * @returns Updated patient
 * @throws ApiError with code "NOT_FOUND" if patient doesn't exist
 *
 * @example
 * const updated = await updatePatient("123...", {
 *   diagnosis: "Generalized Anxiety Disorder"
 * })
 */
export async function updatePatient(
  patientId: string,
  data: UpdatePatientRequest,
  token?: string
): Promise<PatientResponse> {
  return patch<PatientResponse>(`/api/patients/${patientId}`, data, token)
}

/**
 * Delete a patient and all their sessions.
 *
 * Soft-delete that enters the 30-day undo window (THERAPY-yg2). Backend
 * requires `acknowledged_retention_obligation: true` in the request body
 * (THERAPY-9ig); pass the user's attestation through from the delete
 * confirmation modal.
 *
 * @param patientId - Patient ID
 * @param acknowledgedRetentionObligation - User attestation that they have
 *   met their professional retention obligations for this record. Must be
 *   `true` for the request to succeed.
 * @param token - Optional auth token for server-side calls
 * @returns Confirmation message
 * @throws ApiError with code "RETENTION_ATTESTATION_REQUIRED" if attestation
 *   is missing or false
 * @throws ApiError with code "NOT_FOUND" if patient doesn't exist
 */
export async function deletePatient(
  patientId: string,
  acknowledgedRetentionObligation: boolean,
  token?: string,
): Promise<DeletePatientResponse> {
  return del<DeletePatientResponse>(
    `/api/patients/${patientId}`,
    token,
    { acknowledged_retention_obligation: acknowledgedRetentionObligation },
  )
}

/**
 * Restore a soft-deleted patient inside the 30-day undo window
 * (THERAPY-yg2).
 *
 * Reverses a prior `deletePatient` call: clears `deleted_at` on the
 * patient and on the therapy sessions / notes that were cascaded with
 * it, returning the patient to live listings. Session numbers are
 * preserved across the round trip.
 *
 * @param patientId - Patient ID
 * @param token - Optional auth token for server-side calls
 * @returns The restored patient (with `deleted_at: null`)
 * @throws ApiError with code "NOT_FOUND" if the patient is not
 *   soft-deleted, doesn't belong to the caller, or is past the 30-day
 *   undo window (already awaiting hard-purge).
 */
export async function restorePatient(
  patientId: string,
  token?: string
): Promise<PatientResponse> {
  return post<PatientResponse>(`/api/patients/${patientId}/restore`, undefined, token)
}
