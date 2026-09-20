// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Intake submissions, read from the chart.
 *
 * The clinician half of the intake surface. The patient half — the form
 * itself and the POST that files it — is a different client on a different
 * credential, and lives in its own module.
 */

import type { PatientIntakeSubmission } from "@/types/patientIntakeSubmissions"
import { get } from "./client"

/** A patient's intake submissions, newest first. */
export async function listPatientIntakeSubmissions(
  patientId: string,
  token?: string,
): Promise<PatientIntakeSubmission[]> {
  return get<PatientIntakeSubmission[]>(
    `/api/patients/${patientId}/intake-submissions`,
    token,
  )
}
