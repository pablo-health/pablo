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
import { usePeopleTerm } from "@/hooks/usePeopleTerm"

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
  const people = usePeopleTerm()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [calendars, setCalendars] = useState<FollowableCalendar[] | null>(null)
  const [shownAs, setShownAs] = useState<string | null>(null)
  const following = importGranted && followedCalendarId !== null

  // Only while following: the list is for choosing which calendar to read.
  useEffect(() => {
    if (!following) return
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
  }, [following, followedCalendarId])

  const follow = async (calendarId: string | null) => {
    setSaving(true)
    setError(null)
    try {
      const result = await setFollowedCalendar(calendarId)
      // Show the choice at once, rather than the old one until the status
      // comes back.
      setShownAs(result.follow_calendar_id)
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
  const listed = calendars ?? []
  const followedName = listed.find((c) => c.id === selected)?.name
  // Followed, but not among the calendars this connection can read now:
  // unshared or deleted since it was chosen. Said plainly rather than
  // showing another calendar as the one read.
  const unreadable = following && calendars !== null && selected !== null && !followedName

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
      {following && calendars === null && !error ? (
        <p className="pl-6 text-xs text-muted-foreground">Loading your calendars…</p>
      ) : null}
      {following && (listed.length > 1 || unreadable) ? (
        <div className="flex flex-col gap-1 pl-6">
          <label htmlFor="settings-follow-calendar-choice" className="text-xs text-muted-foreground">
            Bring sessions in from
          </label>
          <select
            id="settings-follow-calendar-choice"
            value={selected ?? ""}
            disabled={saving}
            onChange={(event) => follow(event.target.value)}
            className="w-fit rounded-md border border-border bg-card px-1.5 py-0.5 text-xs text-neutral-900"
          >
            {unreadable ? (
              <option value={selected ?? ""} disabled>
                Choose a calendar
              </option>
            ) : null}
            {listed.map((calendar) => (
              <option key={calendar.id} value={calendar.id}>
                {calendar.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
      {unreadable ? (
        <p data-testid="followed-calendar-unreadable" className="pl-6 text-xs text-amber-700">
          Pablo can&rsquo;t read the calendar it was following any more. Choose another.
        </p>
      ) : null}
      {following && followedName ? (
        <p data-testid="followed-calendar-line" className="pl-6 text-xs text-muted-foreground">
          {/* "A full name" stands for the backend's rule: one active chart
              bears it. Shared names, initials and inactive charts are asked
              about, which "asks about others" covers. */}
          Pablo reads the events on <strong className="font-medium">{followedName}</strong>. It
          books the ones titled with a {people.one}&rsquo;s full name, and asks about others that
          look like sessions.
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
