// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Narrative Parser Utility
 *
 * Parse narrative text containing **Label:** patterns into structured blocks.
 * Shared by SOAPViewer (display) and pdfExport (PDF rendering).
 */

export interface NarrativeBlock {
  label: string | null
  content: string
}

export function parseNarrativeBlocks(text: string): NarrativeBlock[] {
  if (!text.trim()) return []

  const parts = text.split(/\*\*([^*]+):\*\*\s*/)

  if (parts.length <= 1) {
    return [{ label: null, content: text.trim() }]
  }

  const blocks: NarrativeBlock[] = []

  if (parts[0].trim()) {
    blocks.push({ label: null, content: parts[0].trim() })
  }

  for (let i = 1; i < parts.length; i += 2) {
    const label = parts[i]
    const content = (parts[i + 1] ?? "").trim()
    if (label) {
      blocks.push({ label, content })
    }
  }

  return blocks
}
