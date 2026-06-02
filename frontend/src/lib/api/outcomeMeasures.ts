// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Outcome measures API client
 *
 * Type-safe wrappers around `/api/outcome-measures/{id}` (get, delete) and
 * the patient-scoped path `/api/patients/{patient_id}/outcome-measures`
 * (create, list). See `app.outcome_measures.router`.
 */

import type {
  CreateOutcomeMeasureRequest,
  OutcomeMeasure,
  OutcomeMeasureListResponse,
} from "@/types/outcomeMeasures"
import { del, get, post } from "./client"

export async function createOutcomeMeasure(
  patientId: string,
  data: CreateOutcomeMeasureRequest,
  token?: string,
): Promise<OutcomeMeasure> {
  return post<OutcomeMeasure>(
    `/api/patients/${patientId}/outcome-measures`,
    data,
    token,
  )
}

export async function listOutcomeMeasures(
  patientId: string,
  instrument?: string,
  token?: string,
): Promise<OutcomeMeasureListResponse> {
  const query = instrument ? `?instrument=${encodeURIComponent(instrument)}` : ""
  return get<OutcomeMeasureListResponse>(
    `/api/patients/${patientId}/outcome-measures${query}`,
    token,
  )
}

export async function fetchOutcomeMeasure(
  measureId: string,
  token?: string,
): Promise<OutcomeMeasure> {
  return get<OutcomeMeasure>(`/api/outcome-measures/${measureId}`, token)
}

export async function deleteOutcomeMeasure(
  measureId: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/outcome-measures/${measureId}`, token)
}
