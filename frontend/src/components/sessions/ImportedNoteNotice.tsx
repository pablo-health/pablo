// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What an imported note is: an earlier visit documented, and signed, in the
 * system where it happened, kept here as a reference copy. It is never signed
 * here, so the page offers no way to. The signer is the document's own words,
 * shown only when the document states one, and never matched to a user.
 */

import { format } from "date-fns"
import { FileInput } from "lucide-react"

export interface ImportedNoteNoticeProps {
  /** The date of service, read from the document. */
  sessionDate: string
  /** Who signed it there, as the document writes it. */
  signer: string | null | undefined
}

export function ImportedNoteNotice({ sessionDate, signer }: ImportedNoteNoticeProps) {
  return (
    <section
      aria-label="Imported note"
      className="flex gap-3 rounded-md border border-neutral-200 bg-neutral-50 px-4 py-3 text-sm"
    >
      <FileInput className="mt-0.5 h-4 w-4 shrink-0 text-neutral-500" aria-hidden />
      <div className="space-y-0.5">
        <p className="font-medium text-neutral-900">Imported</p>
        <p className="text-neutral-700">
          Documented elsewhere for the visit on {format(new Date(sessionDate), "MMMM d, yyyy")}.
        </p>
        {signer && <p className="text-neutral-700">Signed by {signer}</p>}
      </div>
    </section>
  )
}
