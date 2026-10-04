// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { CALENDAR_SETUP_PATH } from "@/components/calendar/connect/CalendarSetupWizard"
import { FollowCalendarPicker } from "@/components/calendar/connect/FollowCalendarPicker"
import { calendarInSentence } from "@/components/calendar/connect/calendarNames"
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
 * "Keep importing new sessions", from a calendar the clinician chooses —
 * the main one unless they pick another. Following reads the calendar's
 * events, which is the "Scan calendar" permission, so without it the
 * setting can't be turned on and offers that permission instead.
 */
export function FollowCalendarSetting({
  followedCalendarId,
  importGranted,
  booksNamedSessions = false,
  onChanged,
}: {
  /** The calendar followed now, or null. */
  followedCalendarId: string | null
  importGranted: boolean
  /** Whether a session whose title is one client's full name books on its
   * own (the setting beside this one), so the line says what happens. */
  booksNamedSessions?: boolean
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

  // Google returns to the setup page, the one redirect registered for it,
  // which finishes the round trip, turns following on and sends the browser
  // back here, where the calendar can be changed.
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
  const followed = listed.find((c) => c.id === selected)
  // Followed, but not among the calendars this connection can read now:
  // unshared or deleted since it was chosen. Said plainly rather than
  // showing another calendar as the one read.
  const unreadable = following && calendars !== null && selected !== null && !followed

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
          Keep importing new sessions
        </label>
      </div>
      {following && calendars === null && !error ? (
        <p className="pl-6 text-xs text-muted-foreground">Loading your calendars…</p>
      ) : null}
      {/* With one calendar there is nothing to choose; the line below names it. */}
      {following && (listed.length > 1 || unreadable) ? (
        <div className="pl-6">
          <FollowCalendarPicker
            id="settings-follow-calendar-choice"
            label="Import sessions from"
            calendars={listed}
            value={selected}
            disabled={saving}
            onPick={(calendarId) => void follow(calendarId)}
            placeholder={unreadable ? "Choose a calendar" : undefined}
          />
        </div>
      ) : null}
      {unreadable ? (
        <p data-testid="followed-calendar-unreadable" className="pl-6 text-xs text-amber-700">
          Pablo can no longer read the calendar it was importing from. Choose another.
        </p>
      ) : null}
      {following && followed ? (
        <p data-testid="followed-calendar-line" className="pl-6 text-xs text-muted-foreground">
          {/* "A full name" stands for the backend's rule: one active chart
              bears it. Shared names, initials and inactive charts are asked
              about, which "asks about others" covers. */}
          Pablo reads <strong className="font-medium">{calendarInSentence(followed)}</strong>
          {booksNamedSessions ? (
            <>
              . It adds sessions that show a {people.one}&rsquo;s full name and asks about
              anything else that looks like a session.
            </>
          ) : (
            <> and asks about anything that looks like a session.</>
          )}
        </p>
      ) : null}
      {!importGranted && (
        <div className="flex flex-wrap items-center gap-2 pl-6 text-xs text-muted-foreground">
          <span>This needs &ldquo;Scan calendar&rdquo; access.</span>
          <Button variant="outline" size="sm" onClick={askForAccess} disabled={saving}>
            Allow access
          </Button>
        </div>
      )}
      {error && <p className="pl-6 text-xs text-red-600">{error}</p>}
    </div>
  )
}
