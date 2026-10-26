// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Words for a client's preferences on the chart. Scheduling values read in
 * the waitlist's words, so a time reads the same on both.
 */

import { entrySummary } from "@/components/waitlist/waitlistCopy"
import {
  AVAILABILITY,
  COMMUNICATION_NOTE,
  HARD_LIMIT,
  PRACTICAL_NOTE,
  scheduleWindow,
  type CoreKind,
  type Preference,
} from "@/types/patientPreferences"

export const ADD = "Add"
export const EDIT = "Edit"
export const RETIRE = "Remove"
export const SAVE = "Save"
export const CANCEL = "Cancel"
export const EMPTY = "Nothing added yet."
export const KIND_LEGEND = "What to add"
export const FIRMNESS_LEGEND = "How firm"
export const PREFERS = "Prefers these times"
export const ONLY = "Only these times"
export const OPTIONAL_WORDS = "In your words (optional)"
export const NOTE_WORDS = "Note"
export const NOTE_REQUIRED = "Add a few words."
export const SAVE_FAILED = "Could not save that. Please try again."
export const RETIRE_FAILED = "Could not remove that. Please try again."
/** The one line that keeps clinical content off this card; the reason is in the design doc. */
export const NOT_CLINICAL = "For practical things. Clinical details belong in a note."

export const KIND_CHOICES: { kind: CoreKind; label: string }[] = [
  { kind: AVAILABILITY, label: "Times that suit" },
  { kind: HARD_LIMIT, label: "Times that don't work" },
  { kind: COMMUNICATION_NOTE, label: "Communication" },
  { kind: PRACTICAL_NOTE, label: "Practical" },
]

export const DOMAIN_LABELS: Record<string, string> = {
  scheduling: "Scheduling",
  communication: "Communication",
  practical: "Practical",
}

export function heading(name: string): string {
  return `How to work with ${name}`
}

export function dialogTitle(name: string, editing: boolean): string {
  return editing ? `Edit for ${name}` : `Add for ${name}`
}

/** "Goes by Mia · they/them": who the client is, from their own record. */
export function identityLine(preferredName?: string | null, pronouns?: string | null): string {
  return [preferredName ? `Goes by ${preferredName}` : null, pronouns || null]
    .filter(Boolean)
    .join(" · ")
}

/** The row's main line: the value in words, or the clinician's note. */
export function preferenceLine(preference: Preference): string {
  const window = scheduleWindow(preference)
  if (window && preference.kind === AVAILABILITY) {
    const lead = preference.strength === "hard" ? "Only" : "Prefers"
    return `${lead}: ${entrySummary({ ...window, session_type: null })}`
  }
  if (window && preference.kind === HARD_LIMIT) {
    return `Can't make: ${entrySummary({ ...window, session_type: null })}`
  }
  if (preference.kind === COMMUNICATION_NOTE || preference.kind === PRACTICAL_NOTE) {
    return preference.text ?? ""
  }
  return `${preference.kind_label}: ${preference.summary ?? preference.text ?? ""}`
}

/** The clinician's words under a structured row, when there are any. */
export function wordsLine(preference: Preference): string | null {
  if (!preference.text) return null
  // Notes are their words already; so is another kind's main line when it has no summary.
  const textIsMainLine =
    preference.kind === COMMUNICATION_NOTE ||
    preference.kind === PRACTICAL_NOTE ||
    (!scheduleWindow(preference) && preference.summary === null)
  return textIsMainLine ? null : `“${preference.text}”`
}

function month(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", { month: "long", year: "numeric" })
}

/** Where a row came from and when: "You said, October 2026". */
export function provenance(preference: Preference): string {
  switch (preference.source) {
    case "learned":
      return `Confirmed, ${month(preference.confirmed_at ?? preference.stated_at)}`
    case "intake":
      return `From intake, ${month(preference.stated_at)}`
    case "migrated":
      return `Added ${month(preference.stated_at)}`
    default:
      return `You said, ${month(preference.stated_at)}`
  }
}
