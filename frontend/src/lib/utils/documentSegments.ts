// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * An imported document's paragraphs, numbered from 0, exactly as the backend
 * numbers them when it asks what the document adds to the chart
 * (`app.chart_proposals.drafting.document_segments`): paragraphs separated by
 * blank lines, or, in a document with none (a Word export puts each paragraph
 * on its own line), a paragraph a line. A proposal's evidence cites these
 * numbers, so the source pane can light the paragraph it means.
 */

import type { TranscriptSegment } from "@/types/sessions"

export function documentParagraphs(text: string): string[] {
  const lines = text.trim().split(/\r?\n/).map((line) => line.trim())
  if (!lines.includes("")) return lines.filter((line) => line !== "")
  const paragraphs: string[] = []
  let current: string[] = []
  for (const line of [...lines, ""]) {
    if (line) {
      current.push(line)
    } else if (current.length) {
      paragraphs.push(current.join("\n"))
      current = []
    }
  }
  return paragraphs
}

/** The paragraphs as segments the transcript pane can list and scroll to. */
export function documentSegments(text: string): TranscriptSegment[] {
  return documentParagraphs(text).map((paragraph, index) => ({
    index,
    speaker: "",
    text: paragraph,
    start_time: 0,
    end_time: 0,
  }))
}
