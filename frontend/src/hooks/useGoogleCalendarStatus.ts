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
 * Whether Pablo can no longer read the connected calendar on its own.
 *
 * A revoked grant needs a reconnect however recent the failure, so it shows
 * at once. Anything else is shown only once scheduled reads have stopped: a
 * single failed read is usually Google being briefly unavailable, and the
 * next read clears it.
 */
export function cannotReadCalendar(status: GoogleCalendarStatus | undefined): boolean {
  if (!status?.connected) return false
  return status.read_error === "access_revoked" || Boolean(status.reads_paused)
}
