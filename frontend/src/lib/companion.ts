// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { isEnabled } from "./featureFlags"

export function isMacOS(): boolean {
  if (typeof navigator === "undefined") return false
  return /Mac/.test(navigator.platform) || /Macintosh/.test(navigator.userAgent)
}

/**
 * True when the companion app launch flow is available to this user:
 * macOS platform AND the companion_mac flag is on.
 *
 * Use this to gate pablohealth:// deep links and "Start session" buttons.
 * On Windows / Linux / mobile the flag being on has no effect — those
 * platforms can't handle the URL scheme.
 */
export function isCompanionAvailable(): boolean {
  return isMacOS() && isEnabled("companion_mac")
}
