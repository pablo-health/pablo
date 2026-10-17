// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useSyncExternalStore } from "react"
import type { PabloNoteLine } from "./pabloNoteLines"
import { isCompanionAvailable } from "@/lib/companion"
import { useConfig } from "@/lib/config"
import { useFeaturePredicate } from "@/lib/featureGates"

/**
 * Pablo-note visibility slot.
 *
 * Returns the predicate `PabloNote` uses to drop lines whose feature is off.
 *
 * A few features are switched by their own runtime toggle rather than by the
 * deployment's feature list, so their names are answered here from the same
 * source the rest of the app reads for them. Every other name goes to
 * `useFeature`'s rules, like the sidebar's `requiresFlag`.
 *
 * The base build ignores `requiresCapability`, having no notion of plans; a
 * downstream build overwrites *this file only* to add that, the same way it
 * does `sidebarVisibility.ts`.
 */
export function usePabloNoteVisibility(): (line: PabloNoteLine) => boolean {
  const config = useConfig()
  const isOn = useFeaturePredicate()
  // The desktop app is detected from the browser, so the server answers no
  // and the client corrects it after hydration, without a mismatch.
  const companion = useSyncExternalStore(noSubscription, isCompanionAvailable, () => false)

  const runtimeToggles: Record<string, boolean> = {
    public_booking: config.publicBookingEnabled,
    google_calendar: config.googleCalendarEnabled,
    passkeys: config.passkeysEnabled,
    companion,
  }

  return (line: PabloNoteLine) => {
    const flag = line.requiresFlag
    if (flag && flag in runtimeToggles) return runtimeToggles[flag]
    return isOn(flag)
  }
}

function noSubscription(): () => void {
  return () => {}
}
