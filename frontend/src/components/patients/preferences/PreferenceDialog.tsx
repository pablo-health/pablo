// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Add or edit one preference. A kind a client has at most one of, and already
 * has, is not offered when adding: the existing row is edited instead.
 *
 * Mounted fresh each time it opens, so it starts from the row being edited or
 * from the first kind still available.
 */

"use client"

import { useId, useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { useAddPreference, useUpdatePreference } from "@/hooks/usePatientPreferences"
import { ApiError } from "@/lib/api/client"
import {
  AVAILABILITY,
  HARD_LIMIT,
  PREFERENCE_TEXT_MAX,
  SINGLE_ACTIVE_KINDS,
  scheduleWindow,
  type CoreKind,
  type Preference,
  type ScheduleWindow,
  type Strength,
} from "@/types/patientPreferences"
import { ScheduleWindowFields, windowProblems } from "./ScheduleWindowFields"
import {
  CANCEL,
  FIRMNESS_LEGEND,
  KIND_CHOICES,
  KIND_LEGEND,
  NOTE_REQUIRED,
  NOTE_WORDS,
  NOT_CLINICAL,
  ONLY,
  OPTIONAL_WORDS,
  PREFERS,
  SAVE,
  SAVE_FAILED,
  dialogTitle,
} from "./preferencesCopy"

interface PreferenceDialogProps {
  patientId: string
  name: string
  /** The row to edit, or `null` to add one. */
  preference: Preference | null
  /** The client's active rows, to know which kinds can still be added. */
  existing: Preference[]
  open: boolean
  onOpenChange: (open: boolean) => void
}

const EMPTY_WINDOW: ScheduleWindow = { days: [], times: [], modality: "either" }

function addableKinds(existing: Preference[]): CoreKind[] {
  const taken = new Set(existing.map((p) => p.kind))
  return KIND_CHOICES.map((c) => c.kind).filter(
    (kind) => !(SINGLE_ACTIVE_KINDS.includes(kind) && taken.has(kind)),
  )
}

export function PreferenceDialog({
  patientId,
  name,
  preference,
  existing,
  open,
  onOpenChange,
}: PreferenceDialogProps) {
  const add = useAddPreference()
  const update = useUpdatePreference()
  const ids = useId()
  const choices = addableKinds(existing)
  const [kind, setKind] = useState<string>(preference?.kind ?? choices[0])
  const [schedule, setSchedule] = useState<ScheduleWindow>(
    (preference && scheduleWindow(preference)) ?? EMPTY_WINDOW,
  )
  const [strength, setStrength] = useState<Strength>(preference?.strength ?? "soft")
  const [text, setText] = useState(preference?.text ?? "")
  const [checked, setChecked] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const scheduling = kind === AVAILABILITY || kind === HARD_LIMIT
  const windowIssues = scheduling ? windowProblems(schedule) : []
  const missingNote = !scheduling && text.trim() === ""

  async function save() {
    setChecked(true)
    setFailure(null)
    if (windowIssues.length > 0 || missingNote) return
    const words = text.trim() || null
    const data = scheduling
      ? { value: schedule, text: words, strength: kind === AVAILABILITY ? strength : "hard" as const }
      : { text: words }
    try {
      if (preference) {
        await update.mutateAsync({ patientId, preferenceId: preference.id, data })
      } else {
        await add.mutateAsync({ patientId, data: { kind, ...data } })
      }
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError && err.handledExternally) {
        onOpenChange(false)
        return
      }
      // A 409 or 422 says why in plain words.
      setFailure(
        err instanceof ApiError && (err.status === 409 || err.status === 422)
          ? err.message
          : SAVE_FAILED,
      )
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="preference-dialog">
        <DialogHeader>
          <DialogTitle>{dialogTitle(name, preference !== null)}</DialogTitle>
          <DialogDescription>{NOT_CLINICAL}</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          {!preference && (
            <fieldset>
              <legend className="mb-1.5 text-sm font-medium text-neutral-900">{KIND_LEGEND}</legend>
              <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                {KIND_CHOICES.filter((c) => choices.includes(c.kind)).map((choice) => (
                  <label key={choice.kind} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name={`${ids}-kind`}
                      checked={kind === choice.kind}
                      onChange={() => setKind(choice.kind)}
                    />
                    {choice.label}
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          {scheduling && (
            <ScheduleWindowFields value={schedule} onChange={setSchedule} showErrors={checked} />
          )}

          {kind === AVAILABILITY && (
            <fieldset>
              <legend className="mb-1.5 text-sm font-medium text-neutral-900">
                {FIRMNESS_LEGEND}
              </legend>
              <div className="flex flex-wrap gap-x-3 gap-y-1.5">
                {(
                  [
                    ["soft", PREFERS],
                    ["hard", ONLY],
                  ] as const
                ).map(([value, label]) => (
                  <label key={value} className="flex items-center gap-1.5 text-sm">
                    <input
                      type="radio"
                      name={`${ids}-strength`}
                      checked={strength === value}
                      onChange={() => setStrength(value)}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </fieldset>
          )}

          <div className="space-y-1.5">
            <label htmlFor={`${ids}-text`} className="block text-sm font-medium text-neutral-900">
              {scheduling ? OPTIONAL_WORDS : NOTE_WORDS}
            </label>
            <Textarea
              id={`${ids}-text`}
              value={text}
              maxLength={PREFERENCE_TEXT_MAX}
              onChange={(event) => setText(event.target.value)}
              aria-describedby={checked && missingNote ? `${ids}-text-error` : undefined}
            />
            {checked && missingNote && (
              <p id={`${ids}-text-error`} className="text-xs text-red-600">
                {NOTE_REQUIRED}
              </p>
            )}
          </div>

          {failure && (
            <p role="alert" className="text-sm text-red-600">
              {failure}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
              {CANCEL}
            </Button>
            <Button type="submit">{SAVE}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
