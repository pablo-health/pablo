// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/** `2026-10-27` as `Tue, Oct 27`, read as a calendar day rather than a UTC instant. */
export function day(iso: string): string {
  const [year, month, date] = iso.split("-").map(Number)
  return new Date(year, month - 1, date).toLocaleDateString("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
  })
}
