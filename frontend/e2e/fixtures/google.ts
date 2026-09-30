// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Drive the stand-in Google (scripts/e2e/fake_google.py): put events on the
 * account's calendars, change them the way the other service that owns them
 * would, and read back what the backend granted, pushed and asked for.
 *
 * The stack points the backend's OAuth and Calendar API calls here through
 * GOOGLE_CALENDAR_BASE_URL, so a spec that connects Google through the real
 * setup page lands on this server's authorization endpoint, which grants
 * what was asked for and sends the browser straight back.
 *
 * Events are in Google's own event shape. A `dateTime` without an offset is
 * read in the slot's `timeZone`, so a spec writes wall-clock times in the
 * practice's zone and lets the stand-in place them.
 */

import { GOOGLE_URL } from "./stack"

export const SCOPE_APP_CALENDAR = "https://www.googleapis.com/auth/calendar.app.created"
export const SCOPE_FREEBUSY = "https://www.googleapis.com/auth/calendar.freebusy"
export const SCOPE_READ_EVENTS = "https://www.googleapis.com/auth/calendar.readonly"

export interface EventTime {
  dateTime: string
  timeZone?: string
}

/** A Google Calendar event, as much of it as a spec needs to write. */
export interface GoogleEvent {
  id?: string
  summary?: string
  start?: EventTime
  end?: EventTime
  status?: string
  /** `["RRULE:FREQ=WEEKLY;COUNT=8"]` makes a series. */
  recurrence?: string[]
  recurringEventId?: string
  extendedProperties?: { private?: Record<string, string> }
}

export interface GoogleCalendarEntry {
  id: string
  summary: string
  primary?: boolean
}

export interface SeenRequest {
  method: string
  path: string
  status: number
}

async function call<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${GOOGLE_URL}${path}`, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`fake google ${method} ${path} → ${response.status} ${await response.text()}`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const google = {
  /**
   * Start over as one account with an empty main calendar and nothing
   * granted. A fresh account name per spec gives the main calendar a new
   * id, so nothing the backend remembers about the last spec's calendar
   * is judged against this one.
   */
  async reset(account: string): Promise<void> {
    await call("POST", "/_fake/reset", { account })
  },

  /** The scopes the account has granted the backend so far, sorted. */
  async grant(): Promise<string[]> {
    return (await call<{ scopes: string[] }>("GET", "/_fake/grant")).scopes
  },

  /** Every request the backend has made to Google's paths, in order. */
  async requests(): Promise<SeenRequest[]> {
    return (await call<{ requests: SeenRequest[] }>("GET", "/_fake/requests")).requests
  },

  async calendars(): Promise<GoogleCalendarEntry[]> {
    return (await call<{ calendars: GoogleCalendarEntry[] }>("GET", "/_fake/calendars")).calendars
  },

  /** A second calendar on the account. */
  async addCalendar(summary: string): Promise<GoogleCalendarEntry> {
    return call("POST", "/_fake/calendars", { summary })
  },

  /** What is on a calendar as stored: a series is one event with a rule. */
  async events(calendarId: string): Promise<GoogleEvent[]> {
    return (
      await call<{ events: GoogleEvent[] }>(
        "GET",
        `/_fake/calendars/${encodeURIComponent(calendarId)}/events`,
      )
    ).events
  },

  /** Put an event on a calendar; `"primary"` is the main one. */
  async seed(calendarId: string, event: GoogleEvent): Promise<GoogleEvent & { id: string }> {
    return call("POST", `/_fake/calendars/${encodeURIComponent(calendarId)}/events`, event)
  },

  /** Change an event, a whole series, or one instance of a series. */
  async change(calendarId: string, eventId: string, patch: GoogleEvent): Promise<GoogleEvent> {
    return call(
      "PATCH",
      `/_fake/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(eventId)}`,
      patch,
    )
  },

  async remove(calendarId: string, eventId: string): Promise<void> {
    await call(
      "DELETE",
      `/_fake/calendars/${encodeURIComponent(calendarId)}/events/${encodeURIComponent(eventId)}`,
    )
  },

  /** Age out every sync token for a calendar: the next resumed read is answered 410. */
  async expireSyncTokens(calendarId: string): Promise<void> {
    await call("POST", `/_fake/calendars/${encodeURIComponent(calendarId)}/expire-sync-tokens`)
  },

  /** Age out every access token: the next API call is answered 401, and a refresh follows. */
  async expireAccessTokens(): Promise<void> {
    await call("POST", "/_fake/expire-access-tokens")
  },
}

/** The zone the stand-in's account keeps, and the practice's default. */
export const CALENDAR_TIME_ZONE = "America/New_York"

/**
 * A wall-clock time on a calendar day `daysAhead` from today, in the
 * practice's zone, for a slot's `dateTime`. The date is read off the
 * practice's clock rather than the runner's, so "tomorrow" here is the
 * calendar page's tomorrow whatever zone the suite runs in.
 */
export function localDateTime(daysAhead: number, time: string): EventTime {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: CALENDAR_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  // A UTC-midnight Date used only as a calendar, so rolling over a
  // daylight-saving change can neither skip nor repeat a date.
  const day = new Date(Date.UTC(part("year"), part("month") - 1, part("day")))
  day.setUTCDate(day.getUTCDate() + daysAhead)
  const date = day.toISOString().slice(0, 10)
  return { dateTime: `${date}T${time}:00`, timeZone: CALENDAR_TIME_ZONE }
}

/** The weekday (0 = Sunday) of the day `daysAhead` from today, on the practice's clock. */
export function localWeekday(daysAhead: number): number {
  const { dateTime } = localDateTime(daysAhead, "12:00")
  return new Date(`${dateTime.slice(0, 10)}T12:00:00Z`).getUTCDay()
}

/**
 * The instant a wall-clock time in the practice's zone names, for a rule
 * that has to be written in UTC (an `UNTIL`). Reads the zone's offset off
 * the clock at that moment, so it is right on either side of a
 * daylight-saving change.
 */
export function toUtc(local: EventTime): Date {
  const asIfUtc = new Date(`${local.dateTime}Z`)
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: local.timeZone ?? CALENDAR_TIME_ZONE,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(asIfUtc)
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value)
  const shownAt = Date.UTC(
    part("year"),
    part("month") - 1,
    part("day"),
    part("hour"),
    part("minute"),
    part("second"),
  )
  // The zone showed `asIfUtc` as `shownAt`; the difference is its offset.
  return new Date(asIfUtc.getTime() - (shownAt - asIfUtc.getTime()))
}

/** A slot's end, `minutes` after its start, in the same zone. */
export function plusMinutes(start: EventTime, minutes: number): EventTime {
  const [date, time] = start.dateTime.split("T")
  const [hours, mins] = time.split(":").map(Number)
  const total = hours * 60 + mins + minutes
  const hh = String(Math.floor(total / 60)).padStart(2, "0")
  const mm = String(total % 60).padStart(2, "0")
  return { dateTime: `${date}T${hh}:${mm}:00`, timeZone: start.timeZone }
}
