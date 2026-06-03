// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Diagnostics API client
 *
 * Type-safe wrappers around the diagnostic-criteria engine: the global
 * definition catalog (`/api/diagnostic-definitions`), the patient-scoped
 * assessment path (`/api/patients/{patient_id}/diagnostic-assessments`), and
 * the by-id get/delete (`/api/diagnostic-assessments/{id}`). See
 * `app.diagnostics.router`.
 */

import type {
  CreateDiagnosticAssessmentRequest,
  DiagnosticAssessment,
  DiagnosticAssessmentListResponse,
  DiagnosticDefinitionListResponse,
} from "@/types/diagnoses"
import { del, get, post } from "./client"

export async function listDiagnosticDefinitions(
  token?: string,
): Promise<DiagnosticDefinitionListResponse> {
  return get<DiagnosticDefinitionListResponse>(
    "/api/diagnostic-definitions",
    token,
  )
}

export async function createDiagnosticAssessment(
  patientId: string,
  data: CreateDiagnosticAssessmentRequest,
  token?: string,
): Promise<DiagnosticAssessment> {
  return post<DiagnosticAssessment>(
    `/api/patients/${patientId}/diagnostic-assessments`,
    data,
    token,
  )
}

export async function listDiagnosticAssessments(
  patientId: string,
  instrument?: string,
  token?: string,
): Promise<DiagnosticAssessmentListResponse> {
  const query = instrument ? `?instrument=${encodeURIComponent(instrument)}` : ""
  return get<DiagnosticAssessmentListResponse>(
    `/api/patients/${patientId}/diagnostic-assessments${query}`,
    token,
  )
}

export async function fetchDiagnosticAssessment(
  assessmentId: string,
  token?: string,
): Promise<DiagnosticAssessment> {
  return get<DiagnosticAssessment>(
    `/api/diagnostic-assessments/${assessmentId}`,
    token,
  )
}

export async function deleteDiagnosticAssessment(
  assessmentId: string,
  token?: string,
): Promise<void> {
  return del<void>(`/api/diagnostic-assessments/${assessmentId}`, token)
}
