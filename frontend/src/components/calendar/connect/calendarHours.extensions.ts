// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Calendar hours step extension slot.
 *
 * `useHoursFromTextAvailable()` answers whether this account can enter its
 * hours as a sentence. `CalendarHoursStep` leads with that box; where the
 * answer is false it opens on the hours grid instead, with no box, no example
 * chips and no way back to them, and never calls the parse route. That is for
 * a deployment that cannot read hours from text for some or all accounts: the
 * parse call would fail, and the step's fallback message ("couldn't read
 * that") would describe a sentence nobody had a chance to write.
 *
 * null means the answer is still loading; the step shows its heading alone
 * until it arrives, so neither the box nor the grid flashes.
 *
 * The default build answers true for every account. A downstream build
 * replaces THIS FILE ONLY to consult its own policy (same pattern as
 * `lib/companion.extensions.ts`). An implementation MAY be a hook and MAY
 * require the app's providers (React Query, runtime config).
 *
 * UI-only: hiding the box does not refuse the parse route; the backend
 * decides that on its own.
 */
export function useHoursFromTextAvailable(): boolean | null {
  return true
}
