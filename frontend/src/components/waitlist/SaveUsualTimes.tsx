// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SaveUsualTimes
 *
 * Shown in the waitlist dialog after an entry is saved whose times differ
 * from the client's usual ones (or the client has none). One tap copies the
 * entry's days, times of day and format to the client's
 * `scheduling.availability`: a new row is a soft preference, an existing row
 * keeps its strength and takes the new times. Closing leaves the record as
 * it was; nothing is written without the tap.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAddPreference, useUpdatePreference } from "@/hooks/usePatientPreferences"
import { ApiError } from "@/lib/api/client"
import { AVAILABILITY, type Preference, type ScheduleWindow } from "@/types/patientPreferences"
import { CLOSE, SAVE_FAILED, entrySummary, onListTitle, saveAsUsualTimes } from "./waitlistCopy"

interface SaveUsualTimesProps {
  patientId: string
  patientName: string
  /** The short name the record uses for the client: "Save as Maya's usual times". */
  shortName: string
  window: ScheduleWindow
  /** The client's active availability row, updated in place when there is one. */
  availability: Preference | null
  onDone: () => void
}

export function SaveUsualTimes({
  patientId,
  patientName,
  shortName,
  window,
  availability,
  onDone,
}: SaveUsualTimesProps) {
  const add = useAddPreference()
  const update = useUpdatePreference()
  const [failure, setFailure] = useState<string | null>(null)
  const pending = add.isPending || update.isPending

  async function saveBack() {
    setFailure(null)
    try {
      if (availability) {
        await update.mutateAsync({
          patientId,
          preferenceId: availability.id,
          data: { value: window },
        })
      } else {
        await add.mutateAsync({
          patientId,
          data: { kind: AVAILABILITY, value: window, strength: "soft" },
        })
      }
      onDone()
    } catch (err) {
      if (err instanceof ApiError && err.handledExternally) {
        onDone()
        return
      }
      setFailure(
        err instanceof ApiError && (err.status === 409 || err.status === 422)
          ? err.message
          : SAVE_FAILED,
      )
    }
  }

  return (
    <div data-testid="waitlist-usual-times">
      <DialogHeader>
        <DialogTitle>{onListTitle(patientName)}</DialogTitle>
        <DialogDescription>{entrySummary({ ...window, session_type: null })}</DialogDescription>
      </DialogHeader>

      {failure && (
        <p role="alert" className="mt-4 text-sm text-red-600">
          {failure}
        </p>
      )}

      <DialogFooter className="mt-4">
        <Button type="button" variant="ghost" onClick={onDone} disabled={pending}>
          {CLOSE}
        </Button>
        <Button
          type="button"
          onClick={() => void saveBack()}
          disabled={pending}
          data-testid="waitlist-save-usual-times"
        >
          {saveAsUsualTimes(shortName)}
        </Button>
      </DialogFooter>
    </div>
  )
}
