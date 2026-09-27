// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every string the refills surface shows a patient, in one place.
 *
 * The status labels describe where the request stands and nothing more.
 * "Received" is what the practice has — it is not a promise that anyone
 * has looked yet. "Sent to your pharmacy" is shown only for `approved`,
 * which is the prescriber's decision, not a pharmacy's confirmation of
 * stock or pickup.
 */

import type { RefillRequestStatus } from "@/lib/api/patientRefills"

export const FORM_HEADING = "Request a refill"
export const MEDICATION_LEGEND = "Medication"
export const SOMETHING_ELSE = "Something else"
export const MEDICATION_NAME_LABEL = "Medication name"
export const PHARMACY_LABEL = "Pharmacy (optional)"
export const NOTE_LABEL = "Anything your prescriber should know?"
export const SUBMIT = "Request refill"
export const SUBMITTING = "Sending…"
export const SUBMIT_FAILED = "That didn't send. Try again."

export const LIST_HEADING = "Your requests"
export const LIST_EMPTY = "You haven't asked for a refill yet."
export const LOAD_FAILED = "Refills aren't loading right now. Try again in a moment."

export const STATUS_LABELS: Record<RefillRequestStatus, string> = {
  requested: "Received",
  approved: "Sent to your pharmacy",
  needs_visit: "Let's talk at your next visit",
  declined: "Not refilled",
}
