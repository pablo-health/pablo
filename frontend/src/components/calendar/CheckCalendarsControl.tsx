// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Loader2, RefreshCw } from "lucide-react"
import { useGoogleCalendarStatus } from "@/hooks/useGoogleCalendarStatus"
import { useSyncCalendarsNow } from "@/hooks/useOutsideSessions"
import type { ICalConnectionStatus } from "@/lib/api/scheduling"
import { formatLastRead, oldestRead } from "./calendarReadTime"

/**
 * "Check calendars": read every calendar Pablo follows now — the Google
 * calendar and any feeds — rather than at the next scheduled read, and
 * refresh what the calendar shows. The time beside it is when they were
 * last read.
 */
export function CheckCalendarsControl({
  feeds,
  onRead,
  timeZone,
}: {
  /** The connected feeds, with when each was last read. */
  feeds: ICalConnectionStatus[]
  /** Called after a read, so the feeds' own times can be fetched again. */
  onRead?: () => void
  timeZone?: string
}) {
  const { data: google } = useGoogleCalendarStatus()
  const check = useSyncCalendarsNow()
  const googleConnected = Boolean(google?.connected)
  if (!googleConnected && feeds.length === 0) return null

  const lastRead = oldestRead([
    ...(googleConnected ? [google?.last_synced_at] : []),
    ...feeds.map((feed) => feed.last_synced_at),
  ])

  return (
    <div className="flex items-center gap-3">
      {check.isError ? (
        <span className="text-sm text-red-600">Couldn&rsquo;t check. Try again in a moment.</span>
      ) : lastRead ? (
        <span data-testid="calendar-last-read" className="text-xs text-neutral-500">
          Last read {formatLastRead(lastRead, new Date(), timeZone)}
        </span>
      ) : null}
      <button
        type="button"
        onClick={() => check.mutate(undefined, { onSettled: () => onRead?.() })}
        disabled={check.isPending}
        className="inline-flex items-center gap-1.5 rounded-md border border-neutral-200 px-3 py-1.5 text-sm text-neutral-600 hover:bg-neutral-50 disabled:opacity-50"
      >
        {check.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <RefreshCw className="h-4 w-4" aria-hidden />
        )}
        Check calendars
      </button>
    </div>
  )
}
