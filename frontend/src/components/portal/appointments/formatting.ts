// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Times, rendered in the practice's frame rather than the browser's.
 *
 * Every formatter here takes the zone explicitly, because the default —
 * whatever the device is set to — is right until somebody travels and then
 * silently wrong. A patient in another timezone reading "2:00 PM" for an
 * appointment their practice booked at 5:00 PM has been told something false
 * by a screen that looked correct.
 *
 * An unparseable instant is returned as it arrived rather than rendered as
 * "Invalid Date". These strings come from the server, so a bad one means the
 * contract moved; showing the raw value makes that visible instead of
 * printing a word that looks like a bug in the browser.
 */

/** A day and a time: "Tue, 3 Mar at 2:00 PM". */
export function formatWhen(iso: string, timeZone: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  })
}

/** The same instant spelled out, for a confirmation screen. */
export function formatWhenLong(iso: string, timeZone: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone,
  })
}

/** Just the clock time, for a grid of openings that share a day. */
export function formatTime(iso: string, timeZone: string): string {
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone })
}

/** The heading over a day's openings: "Tuesday, 3 March". */
export function formatDayHeading(isoDate: string, timeZone: string): string {
  const at = new Date(`${isoDate}T12:00:00Z`)
  if (Number.isNaN(at.getTime())) return isoDate
  return at.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  })
}

/**
 * The calendar date *in the practice's zone* that `at` falls on, as
 * `YYYY-MM-DD`.
 *
 * The slots route takes a local calendar date, not an instant, because
 * working hours are written in the clinician's local time. Deriving that date
 * from the browser's midnight would ask for the wrong day for any patient far
 * enough east or west — which is exactly the class of bug rendering in the
 * practice's zone exists to avoid, arriving through the request instead of
 * the display.
 */
export function practiceDate(at: Date, timeZone: string): string {
  // `en-CA` is ISO-shaped (YYYY-MM-DD), which is what the route parses.
  return at.toLocaleDateString("en-CA", { timeZone })
}

/** `isoDate` moved by whole days, staying a `YYYY-MM-DD` calendar date. */
export function addDays(isoDate: string, days: number): string {
  const at = new Date(`${isoDate}T12:00:00Z`)
  at.setUTCDate(at.getUTCDate() + days)
  return at.toISOString().slice(0, 10)
}

/** Whole days from `from` to `to`, both `YYYY-MM-DD`. Negative when earlier. */
export function daysBetween(from: string, to: string): number {
  const a = new Date(`${from}T12:00:00Z`).getTime()
  const b = new Date(`${to}T12:00:00Z`).getTime()
  return Math.round((b - a) / (24 * 60 * 60 * 1000))
}
