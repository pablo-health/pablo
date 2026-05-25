// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Note } from "@/types/notes"

/**
 * Route for a note: session-bound notes open the recorded session, standalone
 * notes open their patient-scoped edit page.
 */
export function noteHref(patientId: string, note: Note): string {
  return note.session_id
    ? `/dashboard/sessions/${note.session_id}`
    : `/dashboard/patients/${patientId}/notes/${note.id}`
}

export interface NoteStatus {
  label: string
  className: string
}

/** Draft-vs-finalized badge, keyed off `finalized_at`. */
export function noteStatus(note: Note): NoteStatus {
  return note.finalized_at
    ? { label: "Finalized", className: "bg-secondary-100 text-secondary-700" }
    : { label: "Draft", className: "bg-yellow-100 text-yellow-800" }
}

/** Human-readable date-time for a note's most relevant timestamp. */
export function formatNoteDateTime(value: string | null): string {
  if (!value) return "—"
  return new Date(value).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}
