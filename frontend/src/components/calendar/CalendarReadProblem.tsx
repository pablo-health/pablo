// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { AlertCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  cannotReadCalendar,
  GOOGLE_CALENDAR_STATUS_KEY,
  useGoogleCalendarStatus,
} from "@/hooks/useGoogleCalendarStatus"
import { syncCalendarsNow } from "@/lib/api/outsideSessions"
import { queryKeys } from "@/lib/api/queryKeys"
import {
  completeGoogleCalendarConnect,
  getGoogleCalendarAuthUrl,
  type GoogleCalendarSelection,
  type GoogleCalendarStatus,
} from "@/lib/api/scheduling"
import { useAuth } from "@/lib/auth-context"

/** Where Google sends the browser back after a reconnect: the calendar,
 * which finishes it (`useFinishReconnect`) and shows the result. */
export const RECONNECT_RETURN_PATH = "/dashboard/calendar"

/** Google requires the redirect URI to match a registered one exactly, so
 * the selection waits here for the browser to come back, as the setup
 * wizard's does. */
const RECONNECT_KEY = "pablo.calendar-reconnect.selection"

/**
 * What the connection held, asked for again. A grant removed at Google
 * takes every permission with it, so reading events is asked for too when
 * the connection could read them — otherwise a followed calendar would stay
 * unread until it was asked for a second time.
 */
export function reconnectSelection(status: GoogleCalendarStatus): GoogleCalendarSelection {
  return {
    write_target: status.write_target ?? "app_calendar",
    busy: status.busy ?? true,
    event_titling: status.event_titling ?? "initials",
    read_events: Boolean(status.import_granted),
  }
}

function rememberReconnect(selection: GoogleCalendarSelection): void {
  try {
    window.sessionStorage.setItem(RECONNECT_KEY, JSON.stringify(selection))
  } catch {
    // Without session storage the return is not recognised as a reconnect,
    // and the calendar opens as usual; Reconnect can be pressed again.
  }
}

function takeReconnect(): GoogleCalendarSelection | null {
  try {
    const raw = window.sessionStorage.getItem(RECONNECT_KEY)
    window.sessionStorage.removeItem(RECONNECT_KEY)
    return raw ? (JSON.parse(raw) as GoogleCalendarSelection) : null
  } catch {
    return null
  }
}

function returnUri(): string {
  return `${window.location.origin}${RECONNECT_RETURN_PATH}`
}

/**
 * Finish a reconnect when Google sends the browser back to the calendar.
 *
 * Exchanges the code with the selection that started it, then reads the
 * calendars at once: the line clears because a read worked, not because a
 * new grant was stored. Returns an error to show, or null.
 */
export function useFinishReconnect(enabled = true): string | null {
  const { loading: authLoading, user } = useAuth()
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const spent = useRef<string | null>(null)

  useEffect(() => {
    // Wait for sign-in: the exchange needs the token, and the code is single-use.
    if (!enabled || authLoading || !user) return
    const params = new URLSearchParams(window.location.search)
    const code = params.get("code")
    if (!code || spent.current === code) return
    const selection = takeReconnect()
    if (!selection) return
    spent.current = code
    // Drop the one-time code so a refresh doesn't try to spend it again.
    window.history.replaceState(null, "", RECONNECT_RETURN_PATH)
    completeGoogleCalendarConnect(code, params.get("state") ?? "", returnUri(), selection)
      .then(() => syncCalendarsNow())
      .catch(() => setError("Google did not finish reconnecting. Try again."))
      .finally(() => {
        queryClient.invalidateQueries({ queryKey: GOOGLE_CALENDAR_STATUS_KEY })
        queryClient.invalidateQueries({ queryKey: queryKeys.appointments.all })
      })
  }, [enabled, authLoading, user, queryClient])

  return error
}

/**
 * One line when Pablo can no longer read the connected Google Calendar,
 * with the way back. Nothing at all otherwise.
 */
export function CalendarReadProblem({ error = null }: { error?: string | null }) {
  const { data: status } = useGoogleCalendarStatus()
  const [starting, setStarting] = useState(false)
  const [startError, setStartError] = useState<string | null>(null)

  const shown = error ?? startError
  const broken = cannotReadCalendar(status)
  if (!broken && !shown) return null

  const reconnect = async () => {
    if (!status) return
    setStarting(true)
    setStartError(null)
    try {
      const selection = reconnectSelection(status)
      rememberReconnect(selection)
      const { auth_url } = await getGoogleCalendarAuthUrl(returnUri(), selection)
      window.location.assign(auth_url)
    } catch {
      setStartError("Could not reach Google. Try again in a moment.")
      setStarting(false)
    }
  }

  return (
    <div
      role="alert"
      data-testid="calendar-read-problem"
      className="flex flex-wrap items-center gap-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
    >
      <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
      <span className="flex-1">
        {broken ? "Pablo can’t read your Google Calendar." : shown}
        {broken && shown ? <span className="ml-1 text-amber-800">{shown}</span> : null}
      </span>
      {broken ? (
        <Button size="sm" variant="outline" onClick={reconnect} disabled={starting}>
          {starting ? "Opening Google…" : "Reconnect"}
        </Button>
      ) : null}
    </div>
  )
}
