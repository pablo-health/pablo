// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Grounding check for imported notes.
 *
 * Mirrors the backend `check_grounding` (note_import_service): a field's text
 * is "grounded" in the source document when it's a whitespace-normalized
 * substring of the source, or its word-token overlap with the source is at
 * least the threshold (the parse sometimes joins several verbatim passages
 * into one field, which breaks contiguous-substring matching but keeps every
 * word). This lets the review UI flag any field that wasn't found verbatim in
 * the original document — i.e. that the clinician should double-check.
 *
 * Recomputed client-side from the parsed note text + the original document
 * (kept as the session transcript) so no extra state is stored or fetched.
 */

const OVERLAP_THRESHOLD = 0.9

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLowerCase()
}

function wordTokens(value: string): string[] {
  return value.toLowerCase().match(/[a-z0-9]+/g) ?? []
}

/** Whether `value` is grounded in `source` (see module doc). */
export function isTextGrounded(value: string, source: string): boolean {
  const trimmed = value.trim()
  if (!trimmed) return true // empty fields aren't flagged
  if (normalizeWhitespace(source).includes(normalizeWhitespace(trimmed))) {
    return true
  }
  const tokens = wordTokens(trimmed)
  if (tokens.length === 0) return true
  const sourceTokens = new Set(wordTokens(source))
  const overlap =
    tokens.filter((t) => sourceTokens.has(t)).length / tokens.length
  return overlap >= OVERLAP_THRESHOLD
}

/** Grounded when every non-empty item is grounded. */
export function areAllGrounded(values: string[], source: string): boolean {
  return values.every((v) => isTextGrounded(v, source))
}
