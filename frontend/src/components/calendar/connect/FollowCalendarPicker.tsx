// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import type { FollowableCalendar } from "@/lib/api/outsideSessions"
import { calendarTitle } from "./calendarNames"

/** Beside a calendar Pablo made for another setup. A select option carries
 * text only, so the flag is part of its name. */
export function calendarOptionLabel(calendar: FollowableCalendar): string {
  const title = calendarTitle(calendar)
  return calendar.made_by_pablo ? `${title} (another Pablo setup)` : title
}

/**
 * Choosing which calendar sessions are imported from. A calendar Pablo made
 * for another setup (known from Pablo's record or the marker Pablo writes on
 * it, never from its name) is confirmed before it is chosen: its upcoming
 * sessions, Pablo-booked ones included, come in here. The calendar Pablo
 * writes this clinician's sessions to is never offered (the API leaves it
 * out and refuses it).
 */
export function FollowCalendarPicker({
  id,
  label,
  calendars,
  value,
  disabled = false,
  onPick,
  placeholder,
}: {
  id: string
  label: string
  calendars: FollowableCalendar[]
  value: string | null
  disabled?: boolean
  onPick: (calendarId: string) => void
  /** A disabled first option, for a value no longer among `calendars`. */
  placeholder?: string
}) {
  const [confirming, setConfirming] = useState<string | null>(null)

  const choose = (calendarId: string) => {
    const calendar = calendars.find((c) => c.id === calendarId)
    if (calendar?.made_by_pablo) {
      setConfirming(calendarId)
      return
    }
    setConfirming(null)
    onPick(calendarId)
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <select
        id={id}
        // Shows the calendar being confirmed, so the choice reads as made.
        value={confirming ?? value ?? ""}
        disabled={disabled}
        onChange={(event) => choose(event.target.value)}
        className="w-fit rounded-md border border-border bg-card px-1.5 py-0.5 text-xs text-neutral-900"
      >
        {placeholder ? (
          <option value={value ?? ""} disabled>
            {placeholder}
          </option>
        ) : null}
        {calendars.map((calendar) => (
          <option key={calendar.id} value={calendar.id}>
            {calendarOptionLabel(calendar)}
          </option>
        ))}
      </select>
      {confirming ? (
        <div
          role="alertdialog"
          aria-labelledby={`${id}-confirm`}
          className="mt-1 max-w-md space-y-2 rounded-md border border-amber-300 bg-amber-50 p-2.5"
        >
          <p id={`${id}-confirm`} className="text-xs text-neutral-900">
            Pablo made this calendar for another Pablo setup. Importing from it brings in its
            upcoming sessions, including any booked there from now on.
          </p>
          <div className="flex gap-2">
            <Button
              size="sm"
              onClick={() => {
                const chosen = confirming
                setConfirming(null)
                onPick(chosen)
              }}
            >
              Import from it
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  )
}
