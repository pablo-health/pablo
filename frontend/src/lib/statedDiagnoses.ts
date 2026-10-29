// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Values of a `diagnoses` note field: diagnoses as the clinician stated them,
 * each with its code and status kept apart (backend `app/notes/diagnoses.py`).
 */

export interface StatedDiagnosis {
  label: string
  code: string | null
  status: string | null
}

function part(value: unknown): string | null {
  const text = typeof value === "string" ? value.trim() : ""
  return text || null
}

/**
 * A stored value as stated diagnoses. A plain line (a note written before the
 * field had parts) is a diagnosis with no code; it is never split, since
 * guessing which part is the code would invent one.
 */
export function statedDiagnoses(value: unknown): StatedDiagnosis[] {
  if (!Array.isArray(value)) return []
  const out: StatedDiagnosis[] = []
  for (const item of value) {
    if (typeof item === "string") {
      const label = part(item)
      if (label) out.push({ label, code: null, status: null })
    } else if (item && typeof item === "object") {
      const raw = item as Record<string, unknown>
      const label = part(raw.label)
      if (label) out.push({ label, code: part(raw.code), status: part(raw.status) })
    }
  }
  return out
}

/** One diagnosis as a line: `label (code), status`. */
export function diagnosisText(dx: StatedDiagnosis): string {
  let text = dx.label
  if (dx.code) text += ` (${dx.code})`
  if (dx.status) text += `, ${dx.status}`
  return text
}
