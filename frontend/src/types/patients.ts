// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Patient API Types
 *
 * TypeScript types matching backend Patient API contracts.
 * Field names use snake_case to match backend exactly.
 */

/**
 * Patient response from API
 *
 * Represents a patient record with all metadata.
 *
 * `deleted_at` and `restore_deadline` are populated only when the row
 * comes from the recently-deleted listing
 * (`include_deleted=recent`, THERAPY-yg2). Live CRUD reads return
 * `null` for both fields.
 */
export interface PatientResponse {
  id: string
  user_id: string
  first_name: string
  last_name: string
  email: string | null
  phone: string | null
  status: string
  date_of_birth: string | null
  diagnosis: string | null
  session_count: number
  last_session_date: string | null
  next_session_date: string | null
  created_at: string
  updated_at: string
  deleted_at: string | null
  restore_deadline: string | null
}

/**
 * Paginated list of patients
 */
export interface PatientListResponse {
  data: PatientResponse[]
  total: number
  page: number
  page_size: number
}

/**
 * Request payload for creating a new patient
 */
export interface CreatePatientRequest {
  first_name: string
  last_name: string
  email?: string
  phone?: string
  status?: string
  date_of_birth?: string
  diagnosis?: string
}

/**
 * Request payload for updating a patient
 *
 * All fields are optional - only provided fields will be updated.
 */
export interface UpdatePatientRequest {
  first_name?: string
  last_name?: string
  email?: string
  phone?: string
  status?: string
  date_of_birth?: string
  diagnosis?: string
}

/**
 * Response from deleting a patient
 */
export interface DeletePatientResponse {
  message: string
}

/**
 * Query parameters for listing patients
 *
 * `include_deleted: "recent"` switches the listing to soft-deleted
 * patients still inside the 30-day undo window (THERAPY-yg2). Default
 * (omitted) lists only live patients.
 */
export interface PatientListParams {
  search?: string
  search_by?: "first_name" | "last_name"
  include_deleted?: "recent"
}
