// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The note type a transcript is drafted as, and the values that type asks for
 * up front (a psychiatric follow-up needs its place of service). The same
 * session types "New note" offers, less the hand-written ones a draft never
 * fills; a type the subscription doesn't include explains itself instead of
 * being chosen, as it does on the blank-note path.
 */

"use client"

import { Lock } from "lucide-react"
import { NoteInputFields } from "@/components/notes/NoteInputFields"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useToast } from "@/components/ui/Toast"
import { isReviewInput } from "@/lib/mdm"
import { filledInputs } from "@/lib/noteInputs"
import {
  DEFAULT_NOTE_TYPE,
  lockedNoteTypeMessage,
  type NoteInputSchema,
  type NoteTypeSchema,
} from "@/types/noteTypes"

// The dialog's other text boxes are the shared Input; match its look.
const TEXT_FIELD_CLASS =
  "border-input h-9 w-full rounded-md border bg-transparent px-3 py-1 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 aria-invalid:border-destructive"

export interface TranscriptNoteTypeChoice {
  noteType: string
  inputs: Record<string, string>
}

export const DEFAULT_TRANSCRIPT_NOTE_TYPE: TranscriptNoteTypeChoice = {
  noteType: DEFAULT_NOTE_TYPE,
  inputs: {},
}

/** The catalog's types a transcript can be drafted as. */
export function transcriptNoteTypes(catalog: NoteTypeSchema[]): NoteTypeSchema[] {
  return catalog.filter((t) => t.context === "session" && !t.restricted)
}

/** The inputs a draft of this type takes; the MDM choices are made at review. */
export function draftInputs(type: NoteTypeSchema | undefined): NoteInputSchema[] {
  return (type?.inputs ?? []).filter((input) => !isReviewInput(input.key))
}

/** Required inputs still empty. */
export function missingInputs(
  inputs: NoteInputSchema[],
  values: Record<string, string>,
): NoteInputSchema[] {
  const filled = filledInputs(inputs, values)
  return inputs.filter((i) => i.required && !filled[i.key])
}

/**
 * What the upload sends for this choice. Nothing for the default, so a
 * clinician who never picks sends the request they always have.
 */
export function noteTypeFields(
  choice: TranscriptNoteTypeChoice,
  inputs: NoteInputSchema[],
): { note_type?: string; note_inputs?: Record<string, string> } {
  if (choice.noteType === DEFAULT_NOTE_TYPE) return {}
  return {
    note_type: choice.noteType,
    ...(inputs.length > 0 ? { note_inputs: filledInputs(inputs, choice.inputs) } : {}),
  }
}

export interface TranscriptNoteTypeProps {
  types: NoteTypeSchema[]
  value: TranscriptNoteTypeChoice
  onChange: (next: TranscriptNoteTypeChoice) => void
  /** Flag empty required inputs (after a submit attempt). */
  showMissing: boolean
}

export function TranscriptNoteType({
  types,
  value,
  onChange,
  showMissing,
}: TranscriptNoteTypeProps) {
  const { showToast } = useToast()
  const inputs = draftInputs(types.find((t) => t.key === value.noteType))
  const missing = new Set(showMissing ? missingInputs(inputs, value.inputs).map((i) => i.key) : [])

  const pickType = (key: string) => {
    const type = types.find((t) => t.key === key)
    if (type?.is_locked) {
      showToast(lockedNoteTypeMessage(type.label), "info")
      return
    }
    // Inputs belong to one note type; a different type starts with none.
    onChange({ noteType: key, inputs: {} })
  }

  const setInput = (key: string, next: string) =>
    onChange({ ...value, inputs: { ...value.inputs, [key]: next } })

  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="note_type">Note type</Label>
        <Select value={value.noteType} onValueChange={pickType}>
          <SelectTrigger id="note_type" aria-label="Note type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {types.length === 0 ? (
              <SelectItem value={DEFAULT_NOTE_TYPE}>SOAP</SelectItem>
            ) : (
              types.map((t) => (
                <SelectItem key={t.key} value={t.key}>
                  <span className="flex items-center gap-2">
                    {t.is_locked && <Lock className="w-3.5 h-3.5 text-amber-600" />}
                    {t.label}
                  </span>
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
      </div>

      <NoteInputFields
        inputs={inputs}
        values={value.inputs}
        onChange={setInput}
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
    </>
  )
}
