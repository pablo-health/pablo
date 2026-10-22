// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * AddToWaitlistDialog
 *
 * Which days, times of day and format would suit a client better, and what
 * kind of session. The session type starts at the client's usual one.
 *
 * The format is read from past visits (whether each had a video link), which
 * is a proxy rather than a record. So it is filled in only when every past
 * visit was held the same way; with mixed or no history the clinician picks,
 * and the dialog will not save until they do.
 *
 * Mounted fresh each time it opens, so it starts from the defaults.
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
import { useAddToWaitlist } from "@/hooks/useWaitlist"
import { ApiError } from "@/lib/api/client"
import {
  DAY_PARTS,
  WEEKDAYS,
  type DayPart,
  type PatientWaitlist,
  type WaitlistModality,
  type Weekday,
} from "@/types/waitlist"
import {
  ADD_FAILED,
  ANY_SESSION_TYPE,
  CANCEL,
  DAYS_LEGEND,
  DAYS_REQUIRED,
  DAY_LABELS,
  DAY_PART_LABELS,
  MODALITY_CHOICES,
  MODALITY_LEGEND,
  MODALITY_REQUIRED,
  SAVE,
  SESSION_TYPE_CHOICES,
  SESSION_TYPE_LABEL,
  TIMES_LEGEND,
  TIMES_REQUIRED,
  sessionTypeLabel,
} from "./waitlistCopy"

interface AddToWaitlistDialogProps {
  patientId: string
  patientName: string
  defaults: Pick<PatientWaitlist, "usual_session_type" | "past_modalities">
  open: boolean
  onOpenChange: (open: boolean) => void
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

/** A single past format is a fair guess; none or both is not. */
function suggestedModality(past: PatientWaitlist["past_modalities"]): WaitlistModality | null {
  return past.length === 1 ? past[0] : null
}

export function AddToWaitlistDialog({
  patientId,
  patientName,
  defaults,
  open,
  onOpenChange,
}: AddToWaitlistDialogProps) {
  const add = useAddToWaitlist()
  const ids = useId()
  const [days, setDays] = useState<Weekday[]>([])
  const [times, setTimes] = useState<DayPart[]>([])
  const [modality, setModality] = useState<WaitlistModality | null>(
    suggestedModality(defaults.past_modalities),
  )
  const [sessionType, setSessionType] = useState(defaults.usual_session_type ?? "")
  const [checked, setChecked] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)

  const sessionChoices =
    sessionType && !SESSION_TYPE_CHOICES.some((c) => c.value === sessionType)
      ? [...SESSION_TYPE_CHOICES, { value: sessionType, label: sessionTypeLabel(sessionType) }]
      : SESSION_TYPE_CHOICES

  const missingDays = days.length === 0
  const missingTimes = times.length === 0
  const missingModality = modality === null

  async function save() {
    setChecked(true)
    setFailure(null)
    if (missingDays || missingTimes || modality === null) return
    try {
      await add.mutateAsync({
        patientId,
        data: {
          days: WEEKDAYS.filter((day) => days.includes(day)),
          times: DAY_PARTS.filter((part) => times.includes(part)),
          modality,
          session_type: sessionType || null,
        },
      })
      onOpenChange(false)
    } catch (err) {
      if (err instanceof ApiError && err.handledExternally) {
        onOpenChange(false)
        return
      }
      // A 409 says why in plain words: not active, or already on the list.
      setFailure(err instanceof ApiError && err.status === 409 ? err.message : ADD_FAILED)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="waitlist-dialog">
        <DialogHeader>
          <DialogTitle>Add {patientName} to your waitlist</DialogTitle>
          <DialogDescription>Choose the times that would work better.</DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <fieldset aria-describedby={checked && missingDays ? `${ids}-days-error` : undefined}>
            <legend className="mb-1.5 text-sm font-medium text-neutral-900">{DAYS_LEGEND}</legend>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {WEEKDAYS.map((day) => (
                <label key={day} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={days.includes(day)}
                    onChange={() => setDays((current) => toggle(current, day))}
                  />
                  {DAY_LABELS[day]}
                </label>
              ))}
            </div>
            {checked && missingDays && (
              <p id={`${ids}-days-error`} className="mt-1 text-xs text-red-600">
                {DAYS_REQUIRED}
              </p>
            )}
          </fieldset>

          <fieldset aria-describedby={checked && missingTimes ? `${ids}-times-error` : undefined}>
            <legend className="mb-1.5 text-sm font-medium text-neutral-900">{TIMES_LEGEND}</legend>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {DAY_PARTS.map((part) => (
                <label key={part} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={times.includes(part)}
                    onChange={() => setTimes((current) => toggle(current, part))}
                  />
                  {DAY_PART_LABELS[part]}
                </label>
              ))}
            </div>
            {checked && missingTimes && (
              <p id={`${ids}-times-error`} className="mt-1 text-xs text-red-600">
                {TIMES_REQUIRED}
              </p>
            )}
          </fieldset>

          <fieldset
            aria-describedby={checked && missingModality ? `${ids}-modality-error` : undefined}
          >
            <legend className="mb-1.5 text-sm font-medium text-neutral-900">
              {MODALITY_LEGEND}
            </legend>
            <div className="flex flex-wrap gap-x-3 gap-y-1.5">
              {MODALITY_CHOICES.map((choice) => (
                <label key={choice.value} className="flex items-center gap-1.5 text-sm">
                  <input
                    type="radio"
                    name={`${ids}-modality`}
                    value={choice.value}
                    checked={modality === choice.value}
                    onChange={() => setModality(choice.value)}
                  />
                  {choice.label}
                </label>
              ))}
            </div>
            {checked && missingModality && (
              <p id={`${ids}-modality-error`} className="mt-1 text-xs text-red-600">
                {MODALITY_REQUIRED}
              </p>
            )}
          </fieldset>

          <div className="space-y-1.5">
            <label
              htmlFor={`${ids}-session-type`}
              className="block text-sm font-medium text-neutral-900"
            >
              {SESSION_TYPE_LABEL}
            </label>
            <select
              id={`${ids}-session-type`}
              value={sessionType}
              onChange={(event) => setSessionType(event.target.value)}
              className="h-9 w-full rounded-md border border-neutral-300 bg-white px-2 text-sm"
            >
              <option value="">{ANY_SESSION_TYPE}</option>
              {sessionChoices.map((choice) => (
                <option key={choice.value} value={choice.value}>
                  {choice.label}
                </option>
              ))}
            </select>
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
            <Button type="submit" data-testid="waitlist-save" disabled={add.isPending}>
              {SAVE}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
