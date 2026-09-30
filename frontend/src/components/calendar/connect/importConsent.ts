// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

// Markers that ride across the round trip to Google for the "Look at my
// week" permission. Google sends the browser back to the setup page, which
// reads these to know what the trip was for.

/** Set while an incremental IMPORT-capability round trip is in flight, so
 * the code-exchange effect knows this return from Google is "Look at my
 * week" continuing, not a fresh connect. */
const IMPORT_PENDING_KEY = "pablo.calendar-import.pending"

/** Set when the trip was started from the "keep bringing in new sessions"
 * setting, so following is turned on once the permission is granted. */
const FOLLOW_WANTED_KEY = "pablo.calendar-follow.wanted"

function remember(key: string): void {
  try {
    window.sessionStorage.setItem(key, "1")
  } catch {
    // A browser that refuses session storage still completes the grant; it
    // only loses the note of what the grant was for.
  }
}

function recallAndClear(key: string): boolean {
  try {
    const set = window.sessionStorage.getItem(key) === "1"
    window.sessionStorage.removeItem(key)
    return set
  } catch {
    return false
  }
}

export function rememberImportPending(): void {
  remember(IMPORT_PENDING_KEY)
}

export function recallAndClearImportPending(): boolean {
  return recallAndClear(IMPORT_PENDING_KEY)
}

export function rememberFollowWanted(): void {
  remember(FOLLOW_WANTED_KEY)
}

export function recallAndClearFollowWanted(): boolean {
  return recallAndClear(FOLLOW_WANTED_KEY)
}
