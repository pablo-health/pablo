// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import type { PatientIntakeSubmission } from "@/types/patientIntakeSubmissions"
import { listPatientIntakeSubmissions } from "@/lib/api/patientIntakeSubmissions"
import { queryKeys } from "@/lib/api/queryKeys"
import { useAuthQuery } from "./useAuthQuery"

/**
 * A patient's intake submissions, newest first.
 *
 * `retry: false` because the card that reads this hides itself on an error.
 * Retrying would only delay that, and every attempt is an audited read of
 * the patient's own words.
 */
export function usePatientIntakeSubmissions(
  patientId: string | undefined,
  token?: string,
) {
  return useAuthQuery<PatientIntakeSubmission[]>({
    queryKey: queryKeys.patientIntakeSubmissions.byPatient(patientId ?? ""),
    queryFn: () => listPatientIntakeSubmissions(patientId!, token),
    enabled: !!patientId,
    retry: false,
  })
}
