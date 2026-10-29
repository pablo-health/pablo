// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What an imported note is: an earlier visit documented, and signed, in the
 * system where it happened, kept here as a reference copy. It is never signed
 * here, so the page offers no way to. The signer is the document's own words,
 * shown only when the document states one, and never matched to a user.
 *
 * An archive import names the system and what the note was there; a note read
 * from a single document says only that it was documented elsewhere.
 */

import { format } from "date-fns"
import { FileInput } from "lucide-react"
import { sourceSystemName, sourceTitle } from "@/lib/noteDisplay"
import type { NoteSource } from "@/types/notes"

export interface ImportedNoteNoticeProps {
  /** The date of service, read from the document; null for a note with no visit. */
  sessionDate: string | null
  /** Who signed it there, as the document writes it. */
  signer: string | null | undefined
  /** Where an archive import brought it from, when known. */
  source?: NoteSource | null
}

function day(iso: string): string {
  return format(new Date(iso), "MMMM d, yyyy")
}

export function ImportedNoteNotice({ sessionDate, signer, source }: ImportedNoteNoticeProps) {
  const system = sourceSystemName(source?.system)
  const title = sourceTitle(source)
  const visit = source?.visit_date ?? sessionDate
  const written = source?.created_at ?? null
  const signedBy = signer ?? source?.signed_by ?? null
  const signedAt = source?.signed_at ?? null

  // What it was and when: a visit note names its visit; a note with no visit
  // (a chart note, a treatment plan) names the day it was written.
  const what = title
    ? visit
      ? `${title} for the visit on ${day(visit)}.`
      : written
        ? `${title}, written there on ${day(written)}.`
        : `${title}.`
    : visit
      ? `Documented elsewhere for the visit on ${day(visit)}.`
      : "Documented elsewhere."

  return (
    <section
      aria-label="Imported note"
      className="flex gap-3 rounded-md border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm"
    >
      <FileInput className="mt-0.5 h-4 w-4 shrink-0 text-neutral-500" aria-hidden />
      <div className="space-y-0.5">
        <p className="font-medium text-neutral-900">{system ? `Imported from ${system}` : "Imported"}</p>
        <p className="text-neutral-700">{what}</p>
        {signedBy && (
          <p className="text-neutral-700">
            {system ? "Signed there by " : "Signed by "}
            {signedBy}
            {signedAt ? ` on ${day(signedAt)}` : ""}
          </p>
        )}
      </div>
    </section>
  )
}
