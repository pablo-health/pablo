// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { FollowableCalendar } from "@/lib/api/outsideSessions"

/** How the follow API names the main calendar before its id is known. */
const MAIN_CALENDAR_ID = "primary"

/**
 * Google names a person's main calendar after their email address. Pablo
 * calls it the main calendar instead, known by the flag the calendar list
 * carries (or the "primary" alias), never by the shape of its name.
 */
export function isMainCalendar(calendar: Pick<FollowableCalendar, "id" | "primary">): boolean {
  return calendar.primary || calendar.id === MAIN_CALENDAR_ID
}

/** A calendar's name inside a sentence. */
export function calendarInSentence(calendar: FollowableCalendar): string {
  return isMainCalendar(calendar) ? "your main calendar" : calendar.name
}

/** A calendar's name standing alone, as in a picker. */
export function calendarTitle(calendar: FollowableCalendar): string {
  return isMainCalendar(calendar) ? "Main calendar" : calendar.name
}
