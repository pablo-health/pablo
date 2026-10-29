// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { NoteInputSchema } from "@/types/noteTypes"

/** The declared inputs' non-blank values, trimmed — the shape the API takes. */
export function filledInputs(
  inputs: NoteInputSchema[],
  values: Record<string, string>,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const input of inputs) {
    const value = values[input.key]?.trim()
    if (value) out[input.key] = value
  }
  return out
}

/** Whether every required input has a value. */
export function requiredInputsFilled(
  inputs: NoteInputSchema[],
  values: Record<string, string>,
): boolean {
  const filled = filledInputs(inputs, values)
  return inputs.every((i) => !i.required || !!filled[i.key])
}
