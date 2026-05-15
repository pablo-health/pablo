// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

interface PatientChartExtrasProps {
  patientId: string
}

/**
 * Extension slot rendered at the bottom of the patient detail page.
 * Returns null by default; downstream callers may provide an alternate
 * implementation to render additional content.
 */
export function PatientChartExtras(_props: PatientChartExtrasProps) {
  return null
}
