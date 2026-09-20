// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Intake submission types, as a clinician reads them.
 *
 * Mirrors `ClinicianIntakeSubmissionResponse` in `app.routes.patient_intake`.
 * The stored form also holds every PHQ-9 and GAD-7 answer; none of that is
 * here, because scores reach the chart as outcome measures and the same
 * instrument should not render from two sources.
 */

/** One completed intake form. */
export interface PatientIntakeSubmission {
  id: string
  /** ISO-8601. When the patient completed the form, not when it was stored. */
  submitted_at: string
  /**
   * What the patient said about the name and date of birth the chart holds.
   * An attestation, not an adjudication: a "no" is something to read, and
   * it never edited the chart.
   */
  name_confirmed: boolean
  dob_confirmed: boolean
  /** What they said is wrong, in their words. Null when they said nothing. */
  corrections: string | null
  /** The answer to "What brings you in?". */
  reason_text: string
}
