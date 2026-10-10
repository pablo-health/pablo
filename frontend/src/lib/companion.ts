// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { isEnabled } from "./featureFlags"

export function isMacOS(): boolean {
  if (typeof navigator === "undefined") return false
  return /Mac/.test(navigator.platform) || /Macintosh/.test(navigator.userAgent)
}

export function isWindows(): boolean {
  if (typeof navigator === "undefined") return false
  return /Win/.test(navigator.platform) || /Windows/.test(navigator.userAgent)
}

export type CompanionPlatform = "macos" | "windows"

/** The desktop platform the companion app ships for, or null elsewhere. */
export function companionPlatform(): CompanionPlatform | null {
  if (isMacOS()) return "macos"
  if (isWindows()) return "windows"
  return null
}

/**
 * True when the companion app launch flow is available to this user:
 * a supported platform AND that platform's flag is on (companion_mac on
 * macOS, companion_windows on Windows).
 *
 * Use this to gate pablohealth:// deep links and "Start session" buttons.
 * On Linux / mobile the flags have no effect — those platforms can't
 * handle the URL scheme.
 */
export function isCompanionAvailable(): boolean {
  const platform = companionPlatform()
  if (platform === "macos") return isEnabled("companion_mac")
  if (platform === "windows") return isEnabled("companion_windows")
  return false
}
