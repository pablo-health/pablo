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
 * Delete a patient and all their sessions
 *
 * IMPORTANT: This is a destructive operation that deletes:
 * - The patient record
 * - All session transcripts
 * - All SOAP notes
 * - All quality ratings
 *
 * @param patientId - Patient ID
 * @param token - Optional auth token for server-side calls
 * @returns Confirmation message
 * @throws ApiError with code "NOT_FOUND" if patient doesn't exist
 *
 * @example
 * const result = await deletePatient("123...")
 * console.log(result.message) // "Patient and 5 sessions deleted successfully"
 */
export async function deletePatient(
  patientId: string,
  token?: string
): Promise<DeletePatientResponse> {
  return del<DeletePatientResponse>(`/api/patients/${patientId}`, token)
}
