// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PDF Export Utility
 *
 * Generate and download PDF documents for SOAP notes with session metadata.
 * Renders structured **Label:** content patterns with proper formatting.
 */

import jsPDF from "jspdf"
import type { SOAPNoteModel } from "@/types/sessions"
import { parseNarrativeBlocks } from "./narrativeParser"

export interface PDFExportMetadata {
  patient_name: string
  session_number?: number
  session_date: string
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
 * Export SOAP note to PDF with session metadata.
 * Parses **Label:** content patterns and renders labels bold.
 */
export function exportSOAPToPDF(
  meta: PDFExportMetadata,
  soapNote: SOAPNoteModel,
): void {
  const doc = new jsPDF()
  let yPosition = 20

  // Title
  doc.setFontSize(18)
  doc.setFont("helvetica", "bold")
  doc.text("SOAP Note", LEFT_MARGIN, yPosition)
  yPosition += 15

  // Session metadata
  doc.setFontSize(12)
  doc.setFont("helvetica", "normal")
  doc.text(`Patient: ${meta.patient_name}`, LEFT_MARGIN, yPosition)
  yPosition += 7
  if (meta.session_number !== undefined) {
    doc.text(`Session #${meta.session_number}`, LEFT_MARGIN, yPosition)
    yPosition += 7
  }
  doc.text(`Date: ${formatDate(meta.session_date)}`, LEFT_MARGIN, yPosition)
  yPosition += 15

  const pageHeight = doc.internal.pageSize.height

  const sections: Array<{ title: string; content: string }> = [
    { title: "Subjective", content: soapNote.subjective },
    { title: "Objective", content: soapNote.objective },
    { title: "Assessment", content: soapNote.assessment },
    { title: "Plan", content: soapNote.plan },
  ]

  sections.forEach((section) => {
    if (yPosition > pageHeight - MARGIN_BOTTOM) {
      doc.addPage()
      yPosition = 20
    }

    // Section title
    doc.setFontSize(14)
    doc.setFont("helvetica", "bold")
    doc.text(section.title, LEFT_MARGIN, yPosition)
    yPosition += 7

    // Parse sub-fields
    doc.setFontSize(11)
    const blocks = parseNarrativeBlocks(section.content)

    for (const block of blocks) {
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

  const safeName = meta.patient_name.replace(/[^a-z0-9]+/gi, "-").toLowerCase()
  const filename = `soap-note-${safeName}-${meta.session_date.slice(0, 10)}.pdf`
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
