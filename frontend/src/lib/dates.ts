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
