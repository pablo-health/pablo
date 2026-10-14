// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A note type's inputs laid out for a dialog: a label (starred when
 * required), the control, and a line under any required one left empty.
 * Used where a note is started from a dialog (a transcript upload, a blank
 * note), so both ask the same way.
 */

"use client"

import { NoteInputFields } from "@/components/notes/NoteInputFields"
import type { NoteInputSchema } from "@/types/noteTypes"

// The dialogs' other text boxes are the shared Input; match its look.
const TEXT_FIELD_CLASS =
  "border-input h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive"

export interface LabeledNoteInputsProps {
  inputs: NoteInputSchema[]
  values: Record<string, string>
  onChange: (key: string, value: string) => void
  /** Required inputs to flag as empty. */
  missing: ReadonlySet<string>
}

export function LabeledNoteInputs({ inputs, values, onChange, missing }: LabeledNoteInputsProps) {
  return (
    <NoteInputFields
      inputs={inputs}
      values={values}
      onChange={onChange}
      invalidKeys={missing}
      textClassName={TEXT_FIELD_CLASS}
      renderField={(input, control) => (
        <div className="space-y-2">
          <p className="text-sm font-medium leading-none">
            {input.label}
            {input.required && <span className="text-destructive"> *</span>}
          </p>
          {control}
          {missing.has(input.key) && (
            <p className="text-sm text-destructive">{input.label} is required</p>
          )}
        </div>
      )}
    />
  )
}
