// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The details a note type asks for (its declared inputs), on a session's
 * note. They are carried over from the appointment when the session starts;
 * here the clinician can supply or change them after the visit and redraft
 * the note with them. Only the note's copy changes — the appointment keeps
 * what it was booked with.
 *
 * Read-only once the note is signed: a signed note changes only by addendum.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useNoteType } from "@/hooks/useNoteTypes"
import { filledInputs, requiredInputsFilled } from "@/lib/noteInputs"
import type { Note, RedraftEdits, RedraftNoteRequest } from "@/types/notes"
import { RedraftChoiceDialog } from "./RedraftChoiceDialog"

export interface NoteInputsPanelProps {
  note: Note
  /** False once the note is signed, or while nothing may change it. */
  editable: boolean
  /** The clinician has edited the note (saved, or held on the page). */
  hasEdits: boolean
  onRedraft: (data: RedraftNoteRequest) => void
  pending?: boolean
}

const SELECT_CLASS =
  "w-full rounded-md border border-neutral-300 bg-white px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary-500"

const sameValues = (a: Record<string, string>, b: Record<string, string>) =>
  Object.keys(a).length === Object.keys(b).length && Object.keys(a).every((k) => a[k] === b[k])

export function NoteInputsPanel({
  note,
  editable,
  hasEdits,
  onRedraft,
  pending = false,
}: NoteInputsPanelProps) {
  const { data: noteType } = useNoteType(note.note_type, note.note_type_version)
  const saved = note.note_inputs ?? {}
  const [values, setValues] = useState<Record<string, string>>(saved)
  const [choosing, setChoosing] = useState(false)

  const declared = noteType?.inputs ?? []
  if (declared.length === 0) return null

  const redrafting = note.status === "processing"
  const changed = filledInputs(declared, values)
  const canSave =
    editable &&
    !redrafting &&
    !pending &&
    requiredInputsFilled(declared, values) &&
    !sameValues(changed, filledInputs(declared, saved))

  const submit = (edits?: RedraftEdits) => {
    setChoosing(false)
    onRedraft({ note_inputs: changed, ...(edits ? { edits } : {}) })
  }

  return (
    <section className="card space-y-4 p-4" aria-labelledby="note-inputs-heading">
      <div>
        <h3 id="note-inputs-heading" className="text-base font-semibold text-neutral-900">
          Note details
        </h3>
        {editable && (
          <p className="text-sm text-neutral-600">Change one and redraft the note with it.</p>
        )}
      </div>

      {editable ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {declared.map((input) => {
            const id = `note-input-${input.key}`
            const set = (value: string) => setValues((prev) => ({ ...prev, [input.key]: value }))
            return (
              <div key={input.key} className="space-y-1.5">
                <Label htmlFor={id}>
                  {input.label}
                  {input.required && <span className="ml-1 text-neutral-500">(required)</span>}
                </Label>
                {input.kind === "choice" ? (
                  <select
                    id={id}
                    value={values[input.key] ?? ""}
                    onChange={(e) => set(e.target.value)}
                    disabled={redrafting}
                    className={SELECT_CLASS}
                  >
                    <option value="">Choose…</option>
                    {input.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    id={id}
                    value={values[input.key] ?? ""}
                    onChange={(e) => set(e.target.value)}
                    disabled={redrafting}
                  />
                )}
              </div>
            )
          })}
        </div>
      ) : (
        <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
          {declared.map((input) => (
            <div key={input.key} className="contents">
              <dt className="text-neutral-600">{input.label}</dt>
              <dd className="text-neutral-900">{saved[input.key] || "Not provided"}</dd>
            </div>
          ))}
        </dl>
      )}

      {editable && (
        <div className="flex justify-end">
          <Button
            type="button"
            onClick={() => (hasEdits ? setChoosing(true) : submit())}
            disabled={!canSave}
          >
            Save and redraft
          </Button>
        </div>
      )}

      <RedraftChoiceDialog
        open={choosing}
        onOpenChange={setChoosing}
        onConfirm={submit}
        pending={pending}
      />
    </section>
  )
}
