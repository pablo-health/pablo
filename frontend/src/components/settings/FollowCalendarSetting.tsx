// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { CALENDAR_SETUP_PATH } from "@/components/calendar/connect/CalendarSetupWizard"
import {
  rememberFollowWanted,
  rememberImportPending,
} from "@/components/calendar/connect/importConsent"
import {
  listFollowableCalendars,
  setFollowedCalendar,
  type FollowableCalendar,
} from "@/lib/api/outsideSessions"
import { importNeedsConsent, scanCalendarForImport } from "@/lib/api/scheduling"

/** What following "the main calendar" is stored as until a read resolves it. */
const MAIN_CALENDAR = "primary"

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

/**
 * "Keep bringing in new sessions", from a calendar the clinician chooses —
 * the main one unless they pick another. Following reads the calendar's
 * events, which is the "Look at my week" permission, so without it the
 * setting can't be turned on and offers that permission instead.
 */
export function FollowCalendarSetting({
  followedCalendarId,
  importGranted,
  onChanged,
}: {
  /** The calendar followed now, or null. */
  followedCalendarId: string | null
  importGranted: boolean
  onChanged: () => void
}) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [calendars, setCalendars] = useState<FollowableCalendar[]>([])
  const [shownAs, setShownAs] = useState<string | null>(null)
  const following = importGranted && followedCalendarId !== null

  useEffect(() => {
    if (!importGranted) return
    let cancelled = false
    listFollowableCalendars()
      .then((result) => {
        if (cancelled) return
        setCalendars(result.calendars)
        setShownAs(result.follow_calendar_id)
      })
      .catch(() => {
        if (!cancelled) setError("Could not load your calendars. Try again in a moment.")
      })
    return () => {
      cancelled = true
    }
  }, [importGranted, followedCalendarId])

  const follow = async (calendarId: string | null) => {
    setSaving(true)
    setError(null)
    try {
      await setFollowedCalendar(calendarId)
      onChanged()
    } catch {
      setError("Could not save that. Try again in a moment.")
    } finally {
      setSaving(false)
    }
  }

  // The permission is asked for from the setup page, which finishes the
  // round trip, turns following on and shows the week it read.
  const askForAccess = async () => {
    setSaving(true)
    setError(null)
    try {
      const redirectUri = `${window.location.origin}${CALENDAR_SETUP_PATH}`
      const result = await scanCalendarForImport(redirectUri, browserTimeZone())
      if (importNeedsConsent(result)) {
        rememberImportPending()
        rememberFollowWanted()
        window.location.assign(result.auth_url)
        return
      }
      // Already granted after all: nothing stands in the way.
      await follow(MAIN_CALENDAR)
    } catch {
      setError("Could not reach Google. Try again in a moment.")
    } finally {
      setSaving(false)
    }
  }

  const selected = shownAs ?? followedCalendarId
  const followedName = calendars.find((c) => c.id === selected)?.name

  return (
    <div className="space-y-1.5 border-t border-border pt-2">
      <div className="flex items-start gap-2.5">
        <Checkbox
          id="settings-follow-calendar"
          checked={following}
          disabled={saving || !importGranted}
          onCheckedChange={(value) => follow(value === true ? MAIN_CALENDAR : null)}
        />
        <label htmlFor="settings-follow-calendar" className="text-sm text-neutral-900">
          Keep bringing in new sessions
        </label>
      </div>
      {following && calendars.length > 1 ? (
        <div className="pl-6">
          <select
            aria-label="Calendar to bring sessions in from"
            value={selected ?? ""}
            disabled={saving}
            onChange={(event) => follow(event.target.value)}
            className="rounded-md border border-border bg-card px-1.5 py-0.5 text-xs text-neutral-900"
          >
            {calendars.map((calendar) => (
              <option key={calendar.id} value={calendar.id}>
                {calendar.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {following && followedName ? (
        <p data-testid="followed-calendar-line" className="pl-6 text-xs text-muted-foreground">
          Pablo reads the events on <strong className="font-medium">{followedName}</strong> and
          asks about the ones that look like sessions.
        </p>
      ) : null}
      {!importGranted && (
        <div className="flex flex-wrap items-center gap-2 pl-6 text-xs text-muted-foreground">
          <span>This needs &ldquo;Look at my week&rdquo; access.</span>
          <Button variant="outline" size="sm" onClick={askForAccess} disabled={saving}>
            Allow access
          </Button>
        </div>
      )}
      {error && <p className="pl-6 text-xs text-red-600">{error}</p>}
    </div>
  )
}
