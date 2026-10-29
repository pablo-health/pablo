// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { Note, NoteSource } from "@/types/notes"

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

/**
 * Draft-vs-finalized badge, keyed off `finalized_at`. An imported note was
 * signed where it was written, so it is never a draft here.
 */
export function noteStatus(note: Note): NoteStatus {
  if (note.imported) return { label: "Imported", className: "bg-neutral-100 text-neutral-700" }
  return note.finalized_at
    ? { label: "Finalized", className: "bg-secondary-100 text-secondary-700" }
    : { label: "Draft", className: "bg-yellow-100 text-yellow-800" }
}

/** How a records system is named on screen, from the importer's key for it. */
export function sourceSystemName(system: string | null | undefined): string | null {
  if (!system) return null
  return SYSTEM_NAMES[system] ?? system
}

const SYSTEM_NAMES: Record<string, string> = {
  simplepractice: "SimplePractice",
}

/**
 * What a note's title was in the system it came from, in sentence case:
 * "Progress note", "Chart note", "Diagnosis and treatment plan". Null when the
 * source names none.
 */
export function sourceTitle(source: NoteSource | null | undefined): string | null {
  const title = source?.title?.trim()
  if (!title) return null
  return title.charAt(0).toUpperCase() + title.slice(1).toLowerCase()
}

/**
 * The page heading for a note: an imported note is called what it was where
 * it was written; any other reads "<type> note", without doubling the word
 * when the type's label already ends in it ("Psychotherapy note").
 */
export function noteHeading(typeLabel: string, note: Pick<Note, "source">): string {
  return sourceTitle(note.source) ?? withNoteSuffix(typeLabel)
}

export function withNoteSuffix(typeLabel: string): string {
  return /\bnote$/i.test(typeLabel.trim()) ? typeLabel : `${typeLabel} note`
}

/**
 * Where a note came from, for a list's Source column: the system it was
 * imported from, else whether it hangs off a session.
 */
export function noteOrigin(note: Note): string {
  const system = sourceSystemName(note.source?.system)
  if (system) return `Imported from ${system}`
  if (note.imported) return "Imported"
  return note.session_id ? "Session" : "Standalone"
}

/**
 * The date a list shows for a note: the visit an imported note records, else
 * when it was finalized, else when it last changed.
 */
export function noteListDate(note: Note): string | null {
  return note.source?.visit_date ?? note.finalized_at ?? note.updated_at
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
