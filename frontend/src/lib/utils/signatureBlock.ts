// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The words of a note's signature block, shared by the note view and the PDF
 * so both say exactly the same thing.
 */

import type { NoteSigningRecord } from "@/types/notes"
import type { PDFSignatureBlock } from "./pdfExport"

export function signedByLine(name: string, credentials: string | null | undefined): string {
  const creds = credentials?.trim()
  return `Electronically signed by ${name}${creds ? `, ${creds}` : ""}`
}

/**
 * A moment with its zone named, e.g. "Oct 5, 2026, 3:04 PM EDT". Some ICU
 * versions put a narrow no-break space before "PM", which the PDF's standard
 * font cannot draw, so it is normalized to a plain space.
 */
export function formatSignedAt(iso: string, timeZone: string): string {
  return new Date(iso)
    .toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZone,
      timeZoneName: "short",
    })
    .replace(/ /g, " ")
}

/** A day, e.g. "Oct 5, 2026". */
export function formatDay(iso: string, timeZone: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone,
  })
}

export function amendmentLine(unlockedAt: string, reason: string, timeZone: string): string {
  return `Amended ${formatDay(unlockedAt, timeZone)}: ${reason}`
}

/**
 * The block as the PDF prints it, or undefined when there is nothing to print
 * (an unsigned note with no history). A note finalized before signatures
 * existed prints "Finalized <date>" and no signature line.
 */
export function pdfSignatureBlock(
  record: NoteSigningRecord | undefined,
  timeZone: string,
): PDFSignatureBlock | undefined {
  if (!record) return undefined
  const { signature, finalized_at, versions, addenda } = record
  const lines = signature
    ? [
        signedByLine(signature.signer_name, signature.signer_credentials),
        formatSignedAt(signature.signed_at, timeZone),
      ]
    : finalized_at
      ? [`Finalized ${formatDay(finalized_at, timeZone)}`]
      : []
  const amendments = versions
    .filter((v) => v.unlocked_at && v.unlock_reason)
    .map((v) => amendmentLine(v.unlocked_at!, v.unlock_reason!, timeZone))
  if (lines.length === 0 && amendments.length === 0 && addenda.length === 0) return undefined
  return {
    lines,
    amendments,
    addenda: addenda.map((a) => ({
      text: a.text,
      lines: [
        signedByLine(a.signer_name, a.signer_credentials),
        formatSignedAt(a.created_at, timeZone),
      ],
    })),
  }
}
