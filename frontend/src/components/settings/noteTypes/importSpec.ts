// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Read a note-type definition someone pasted or uploaded as JSON.
 *
 * Accepts the body the save route takes, a template file (`{spec: ...}`), or
 * what the single-type read returns for a practice type (`{spec: ...}` again).
 * Only the shape is checked here — enough to open it in the editor. The
 * server's validation still runs on save, and its messages land on the fields.
 */

import type { PracticeNoteTypeSpec } from "@/types/noteTypes"

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value)
const text = (value: unknown): string => (typeof value === "string" ? value : "")
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])

export type ImportResult = { spec: PracticeNoteTypeSpec } | { error: string }

export function importSpec(raw: string): ImportResult {
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { error: "That isn't valid JSON." }
  }
  const body = isObject(parsed) && isObject(parsed.spec) ? parsed.spec : parsed
  if (!isObject(body) || !text(body.label) || list(body.sections).length === 0) {
    return { error: "That doesn't look like a note type. It needs a label and at least one section." }
  }
  return {
    spec: {
      label: text(body.label),
      description: text(body.description),
      system_prompt: text(body.system_prompt),
      user_template: typeof body.user_template === "string" ? body.user_template : null,
      sections: list(body.sections).filter(isObject).map((s) => ({
        key: text(s.key),
        label: text(s.label),
        fields: list(s.fields).filter(isObject).map((f) => ({
          key: text(f.key),
          label: text(f.label),
          kind: f.kind === "list" ? "list" : "text",
          ai_hint: text(f.ai_hint),
        })),
      })),
      inputs: list(body.inputs).filter(isObject).map((i) => ({
        key: text(i.key),
        label: text(i.label),
        kind: i.kind === "choice" ? "choice" : "text",
        options: list(i.options).map(String),
        required: i.required === true,
      })),
    },
  }
}
