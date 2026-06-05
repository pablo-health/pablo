// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Medication list API types
 *
 * Mirrors backend `app.medications.schemas`. A medication record tracks what a
 * patient is currently prescribed, when it was started or stopped, and the
 * clinical notes around it.
 */

export type MedicationStatus = "active" | "discontinued" | "on_hold"

export interface Medication {
  id: string
  patient_id: string
  drug_name: string
  dose: string
  status: MedicationStatus
  started_at: string | null
  stopped_at: string | null
  stop_reason: string | null
  notes: string | null
  created_by: string
  created_at: string
  updated_at: string
}

export interface MedicationListResponse {
  data: Medication[]
  total: number
}

export interface CreateMedicationRequest {
  drug_name: string
  dose: string
  status?: MedicationStatus
  started_at?: string | null
  stop_reason?: string | null
  notes?: string | null
}

export interface UpdateMedicationRequest {
  drug_name?: string
  dose?: string
  status?: MedicationStatus
  started_at?: string | null
  stopped_at?: string | null
  stop_reason?: string | null
  notes?: string | null
}
