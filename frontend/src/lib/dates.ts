// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Calendar dates for display.
 *
 * Each screen keeps the locale and shape it has always shown: pass the
 * options and, where a screen pins one, the locale. Leaving the locale
 * out uses the browser's.
 */

/** "Mar 4, 2026" in en-US. */
export const MEDIUM_DATE: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "short",
  day: "numeric",
}

/** "March 4, 2026" in en-US. */
export const LONG_DATE: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "long",
  day: "numeric",
}

/** "3/4/2026" in en-US; the same as `toLocaleDateString()` with no options. */
export const NUMERIC_DATE: Intl.DateTimeFormatOptions = {
  year: "numeric",
  month: "numeric",
  day: "numeric",
}

export function formatDate(
  iso: string,
  options: Intl.DateTimeFormatOptions = MEDIUM_DATE,
  locale?: string
): string {
  return new Date(iso).toLocaleDateString(locale, options)
}

/** As {@link formatDate}, but a value that is not a date is shown as given. */
export function formatDateOrRaw(
  iso: string,
  options: Intl.DateTimeFormatOptions = MEDIUM_DATE,
  locale?: string
): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleDateString(locale, options)
}

/** The zone the browser is running in, e.g. "America/Chicago". */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone
}

/**
 * A stored civil date ("YYYY-MM-DD") shown as the calendar date it names,
 * not shifted through UTC. A value that is not one is shown as given.
 */
export function formatCivilDate(
  civil: string,
  options: Intl.DateTimeFormatOptions = MEDIUM_DATE,
  locale?: string
): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(civil)
  if (!match) return civil
  const [year, month, day] = match.slice(1).map(Number)
  return new Date(year, month - 1, day).toLocaleDateString(locale, options)
}

/**
 * The calendar date `at` falls on in `timeZone` (the browser's when omitted),
 * as "YYYY-MM-DD".
 */
export function civilDate(at: Date, timeZone?: string): string {
  // `en-CA` is ISO-shaped (YYYY-MM-DD).
  return at.toLocaleDateString("en-CA", { timeZone })
}

/** Today's date in `timeZone` (the browser's when omitted), as "YYYY-MM-DD". */
export function todayCivilDate(timeZone?: string): string {
  return civilDate(new Date(), timeZone)
}

/** "09:30" as minutes since midnight (570); null for anything else. */
export function clockToMinutes(value: unknown): number | null {
  const match = /^(\d{1,2}):(\d{2})/.exec(String(value ?? ""))
  if (!match) return null
  return Number(match[1]) * 60 + Number(match[2])
}
