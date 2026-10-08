// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What a note's PDF prints, for every note type that isn't SOAP (SOAP's
 * sections come from `soapNotePdf`). Built from the values the note shows on
 * screen — the clinician's edit when there is one — and read with the same
 * helpers the view uses, so the two say the same thing.
 */

import type { PeopleWords } from "@/lib/peopleTerm"
import { inTheNote, isEmptyValue, listItems, textValue } from "@/lib/schemaNoteValues"
import { diagnosisText, statedDiagnoses } from "@/lib/statedDiagnoses"
import type { PDFBlock, PDFNote } from "@/lib/utils/pdfExport"
import { addOnBand, clientPresentLineText, visitLineText } from "@/lib/visitTimes"
import type { NoteFieldKind } from "@/types/noteTypes"
import type { SchemaSectionValues } from "@/types/sessions"
import type { VisitTimes } from "@/types/visitTimes"

/**
 * The layout a schema note prints from: a built-in type's definition, or a
 * practice type's (its stored spec has the same sections and fields).
 */
export interface PDFNoteLayout {
  label: string
  sections: Array<{
    key: string
    label: string
    fields: Array<{ key: string; label: string; kind: NoteFieldKind }>
    review_only?: boolean
  }>
}

const NO_CONTENT = "No content"

const bullets = (items: string[]) => items.map((item) => `- ${item}`).join("\n")

function fieldText(kind: NoteFieldKind, value: unknown): string {
  if (kind === "list") return bullets(listItems(value))
  if (kind === "diagnoses") return bullets(statedDiagnoses(value).map(diagnosisText))
  return textValue(value)
}

/**
 * A schema note's sections and fields, in the type's order and with its
 * labels. A field with nothing in it is left out, as on screen, and a
 * section left with no fields says so; a value is printed as stored, so a
 * field the draft filled with "Not stated." prints that.
 */
export function schemaNotePdf(
  layout: PDFNoteLayout,
  sections: Record<string, SchemaSectionValues>,
): PDFNote {
  return {
    title: layout.label,
    sections: inTheNote(layout.sections).map((section) => {
      const values = sections[section.key] ?? {}
      const blocks: PDFBlock[] = section.fields
        .filter((field) => !isEmptyValue(values[field.key]))
        .map((field) => ({ label: field.label, content: fieldText(field.kind, values[field.key]) }))
      return {
        title: section.label,
        blocks: blocks.length > 0 ? blocks : [{ label: null, content: NO_CONTENT }],
      }
    }),
  }
}

/** A narrative note: its body, under no heading of its own. */
export function narrativeNotePdf(body: string): PDFNote {
  return {
    title: "Narrative Note",
    sections: [{ title: "", blocks: [{ label: null, content: body.trim() || NO_CONTENT }] }],
  }
}

/**
 * The visit's times as the note's PDF prints them, worded as the visit times
 * panel words them: when the visit started and ended, how long the client
 * was on the recording, the psychotherapy minutes once the clinician has
 * confirmed them, and the total time with documentation where it applies.
 */
export function visitPdfLines(
  times: VisitTimes,
  timeZone: string,
  people: PeopleWords,
): string[] {
  const lines: string[] = []
  const visit = visitLineText(times, timeZone)
  if (visit) lines.push(visit)
  const present = clientPresentLineText(
    { ...times, started_at: times.recording_started_at },
    timeZone,
    people,
  )
  if (present) lines.push(present)
  const window = times.psychotherapy
  if (window?.offered && window.confirmed_minutes !== null && window.window_text) {
    lines.push(`Psychotherapy time: ${window.window_text} · ${addOnBand(window.confirmed_minutes)}`)
  }
  if (times.total_with_documentation_minutes !== null) {
    lines.push(
      `Total time on this date, including documentation: ${times.total_with_documentation_minutes} min`,
    )
  }
  return lines
}
