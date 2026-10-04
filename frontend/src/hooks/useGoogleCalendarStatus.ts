// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { getGoogleCalendarStatus, type GoogleCalendarStatus } from "@/lib/api/scheduling"
import { useAuthQuery } from "./useAuthQuery"

/** The key Settings and the setup wizard already read the status under. */
export const GOOGLE_CALENDAR_STATUS_KEY = ["google-calendar", "status"] as const

/** The Google Calendar connection, when it was last read, and whether that worked. */
export function useGoogleCalendarStatus(enabled = true) {
  return useAuthQuery({
    queryKey: GOOGLE_CALENDAR_STATUS_KEY,
    queryFn: getGoogleCalendarStatus,
    enabled,
  })
}

/**
 * The followed calendar is no longer there to read: deleted, or unshared
 * from the account. Connecting again cannot bring it back; choosing another
 * calendar can. It never clears on its own, so it shows from the first read.
 */
export function followedCalendarGone(status: GoogleCalendarStatus | undefined): boolean {
  return Boolean(status?.connected) && status?.read_error === "calendar_not_found"
}

/**
 * Pablo can no longer read the connection, and connecting again is the fix.
 *
 * A revoked grant needs a reconnect however recent the failure, so it shows
 * at once. Any other failure (but a missing calendar) shows only once
 * scheduled reads have stopped: a single failed read is usually Google
 * being briefly unavailable, and the next read clears it.
 */
export function needsReconnect(status: GoogleCalendarStatus | undefined): boolean {
  if (!status?.connected || followedCalendarGone(status)) return false
  return status.read_error === "access_revoked" || Boolean(status.reads_paused)
}

/** Whether Pablo can no longer read the connected calendar on its own. */
export function cannotReadCalendar(status: GoogleCalendarStatus | undefined): boolean {
  return needsReconnect(status) || followedCalendarGone(status)
}
