// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PDF Export Utility
 *
 * One renderer for a note of any type: a title, the visit's details, the
 * note's sections with each field's label in bold, then the signature block.
 * What goes in each section comes from `lib/notePdf`, which builds it from
 * the same values the note shows on screen.
 */

import jsPDF from "jspdf"
import type { PeopleWords } from "@/lib/peopleTerm"
import type { SOAPNoteModel } from "@/types/sessions"
import { parseNarrativeBlocks } from "./narrativeParser"

export interface PDFExportMetadata {
  patient_name: string
  session_number?: number
  session_date: string
  /**
   * The visit's times and confirmed psychotherapy minutes, already worded
   * (see `visitPdfLines`); omitted or empty when there are none.
   */
  visit?: string[]
  /** The signature block under the note; omitted for a note never signed. */
  signature?: PDFSignatureBlock
}

/** One field of a section: its label (null for a body without one) and its text. */
export interface PDFBlock {
  label: string | null
  /** Lines starting with "- " print as bullets. */
  content: string
}

export interface PDFSection {
  /** The section's heading; empty for a note that is one body. */
  title: string
  blocks: PDFBlock[]
}

/** A note as its PDF prints it. */
export interface PDFNote {
  /** The heading, and the start of the file name. */
  title: string
  sections: PDFSection[]
}

/** A signature block, already worded (see `lib/utils/signatureBlock`). */
export interface PDFSignatureBlock {
  /** "Electronically signed by …" and its date, or "Finalized <date>". */
  lines: string[]
  /** "Amended <date>: <reason>", one per time the note was unlocked. */
  amendments: string[]
  addenda: Array<{ text: string; lines: string[] }>
}

const LEFT_MARGIN = 20
const BULLET_INDENT = 28
const MAX_WIDTH = 170
const BULLET_MAX_WIDTH = 162
const LINE_HEIGHT = 5
const SECTION_GAP = 10
const MARGIN_BOTTOM = 20

function formatDate(dateString: string): string {
  const date = new Date(dateString)
  return date.toLocaleDateString("en-US", {
    month: "long",
    day: "numeric",
    year: "numeric",
  })
}

/**
 * Render a single content block into the PDF document.
 * Handles bullet lists (lines starting with "- ") with indentation,
 * and wraps long text within the page width.
 */
function renderContentBlock(
  doc: jsPDF,
  content: string,
  yPosition: number,
  pageHeight: number
): number {
  let y = yPosition
  const contentLines = content.split("\n")

  for (const rawLine of contentLines) {
    const trimmedLine = rawLine.trim()
    if (!trimmedLine) continue

    const isBullet = trimmedLine.startsWith("- ")
    const xPos = isBullet ? BULLET_INDENT : LEFT_MARGIN
    const maxW = isBullet ? BULLET_MAX_WIDTH : MAX_WIDTH
    const displayText = isBullet ? `\u2022 ${trimmedLine.slice(2)}` : trimmedLine

    const wrapped: string[] = doc.splitTextToSize(displayText, maxW)
    for (const wrappedLine of wrapped) {
      if (y > pageHeight - MARGIN_BOTTOM) {
        doc.addPage()
        y = 20
      }
      doc.text(wrappedLine, xPos, y)
      y += LINE_HEIGHT
    }
  }

  return y
}

/**
 * The signature block, any amendment lines under it, then each addendum with
 * its own signature. Returns the y position after the last line.
 */
function renderSignatureBlock(
  doc: jsPDF,
  block: PDFSignatureBlock,
  yPosition: number,
  pageHeight: number,
): number {
  let y = yPosition
  doc.setFontSize(11)
  doc.setFont("helvetica", "normal")
  y = renderContentBlock(doc, [...block.lines, ...block.amendments].join("\n"), y, pageHeight)

  for (const addendum of block.addenda) {
    y += 6
    if (y > pageHeight - MARGIN_BOTTOM) {
      doc.addPage()
      y = 20
    }
    doc.setFont("helvetica", "bold")
    doc.text("Addendum", LEFT_MARGIN, y)
    y += LINE_HEIGHT
    doc.setFont("helvetica", "normal")
    y = renderContentBlock(doc, addendum.text, y, pageHeight)
    y = renderContentBlock(doc, addendum.lines.join("\n"), y, pageHeight)
  }
  return y
}

/** A SOAP note's sections, each sub-field split out from its **Label:** text. */
export function soapNotePdf(soapNote: SOAPNoteModel): PDFNote {
  return {
    title: "SOAP Note",
    sections: [
      { title: "Subjective", content: soapNote.subjective },
      { title: "Objective", content: soapNote.objective },
      { title: "Assessment", content: soapNote.assessment },
      { title: "Plan", content: soapNote.plan },
    ].map(({ title, content }) => ({ title, blocks: parseNarrativeBlocks(content) })),
  }
}

/** Export a SOAP note to PDF with session metadata. */
export function exportSOAPToPDF(
  meta: PDFExportMetadata,
  soapNote: SOAPNoteModel,
  people: PeopleWords,
): void {
  exportNoteToPDF(meta, soapNotePdf(soapNote), people)
}

/** Export a note of any type to PDF with its session metadata. */
export function exportNoteToPDF(meta: PDFExportMetadata, note: PDFNote, people: PeopleWords): void {
  const doc = new jsPDF()
  let yPosition = 20

  // Title
  doc.setFontSize(18)
  doc.setFont("helvetica", "bold")
  doc.text(note.title, LEFT_MARGIN, yPosition)
  yPosition += 15

  // Session metadata
  doc.setFontSize(12)
  doc.setFont("helvetica", "normal")
  doc.text(`${people.One}: ${meta.patient_name}`, LEFT_MARGIN, yPosition)
  yPosition += 7
  if (meta.session_number !== undefined) {
    doc.text(`Session #${meta.session_number}`, LEFT_MARGIN, yPosition)
    yPosition += 7
  }
  doc.text(`Date: ${formatDate(meta.session_date)}`, LEFT_MARGIN, yPosition)
  yPosition += 7
  for (const line of meta.visit ?? []) {
    for (const wrapped of doc.splitTextToSize(line, MAX_WIDTH) as string[]) {
      doc.text(wrapped, LEFT_MARGIN, yPosition)
      yPosition += 7
    }
  }
  yPosition += 8

  const pageHeight = doc.internal.pageSize.height

  note.sections.forEach((section) => {
    if (yPosition > pageHeight - MARGIN_BOTTOM) {
      doc.addPage()
      yPosition = 20
    }

    // Section title; a note that is one body has none.
    if (section.title) {
      doc.setFontSize(14)
      doc.setFont("helvetica", "bold")
      doc.text(section.title, LEFT_MARGIN, yPosition)
      yPosition += 7
    }

    doc.setFontSize(11)
    for (const block of section.blocks) {
      if (yPosition > pageHeight - MARGIN_BOTTOM) {
        doc.addPage()
        yPosition = 20
      }

      if (block.label) {
        // Sub-field label in bold
        doc.setFont("helvetica", "bold")
        doc.text(`${block.label}:`, LEFT_MARGIN, yPosition)
        yPosition += LINE_HEIGHT

        // Sub-field content in normal weight
        doc.setFont("helvetica", "normal")
        if (block.content) {
          yPosition = renderContentBlock(doc, block.content, yPosition, pageHeight)
        }
      } else {
        // Plain text without a label
        doc.setFont("helvetica", "normal")
        yPosition = renderContentBlock(doc, block.content, yPosition, pageHeight)
      }

      yPosition += 2 // Small gap between sub-fields
    }

    yPosition += SECTION_GAP
  })

  if (meta.signature) {
    renderSignatureBlock(doc, meta.signature, yPosition, pageHeight)
  }

  const safeName = meta.patient_name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()
  const kind = note.title.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase()
  const filename = `${kind}-${safeName}-${meta.session_date.slice(0, 10)}.pdf`
  const pdfBlob = doc.output("blob")
  // Force octet-stream MIME type so the browser downloads instead of opening inline
  const downloadBlob = new Blob([pdfBlob], { type: "application/octet-stream" })
  const url = URL.createObjectURL(downloadBlob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}
