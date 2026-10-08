// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * How a schema note's stored values read, on screen and in its PDF. The two
 * share these so a field shown one way on screen is never printed another.
 */

/** A stored value as display lines for a `list` field. */
export function listItems(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v)).filter((v) => v.trim())
  if (typeof value === "string") return value.split("\n").filter((v) => v.trim())
  return []
}

/** A stored value as display text for a `text` field. */
export function textValue(value: unknown): string {
  if (typeof value === "string") return value
  if (Array.isArray(value)) return value.map((v) => String(v)).join("\n")
  if (value == null) return ""
  return JSON.stringify(value, null, 2)
}

/** True for a value with nothing to show; such a field is left out of the view. */
export function isEmptyValue(value: unknown): boolean {
  if (value == null) return true
  if (typeof value === "string") return !value.trim()
  if (Array.isArray(value)) return listItems(value).length === 0
  if (typeof value === "object") return Object.keys(value).length === 0
  return false
}

/**
 * The sections that are part of the note. A review-only section (the
 * evidence behind the medical decision making) is shown beside the note,
 * never in it or in its PDF.
 */
export function inTheNote<S extends { review_only?: boolean }>(sections: S[]): S[] {
  return sections.filter((section) => !section.review_only)
}
