// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Simple feature flags for gating unreleased UI.
 *
 * Override at build time via NEXT_PUBLIC_FF_<FLAG>=true in env,
 * e.g. NEXT_PUBLIC_FF_SESSION_DEFAULTS=true
 */

const FLAGS = {
  session_defaults: true,
  transcription: false,
  calendar_integrations: true,
  audio_retention: false,
  companion_mac: true,
} as const satisfies Record<string, boolean>

export type FeatureFlag = keyof typeof FLAGS

/**
 * Next inlines a NEXT_PUBLIC_* variable into the browser bundle only where the
 * code names it literally, so each override is spelled out: a key built at
 * run time would read as unset in every client component.
 */
const OVERRIDES: Record<FeatureFlag, string | undefined> = {
  session_defaults: process.env.NEXT_PUBLIC_FF_SESSION_DEFAULTS,
  transcription: process.env.NEXT_PUBLIC_FF_TRANSCRIPTION,
  calendar_integrations: process.env.NEXT_PUBLIC_FF_CALENDAR_INTEGRATIONS,
  audio_retention: process.env.NEXT_PUBLIC_FF_AUDIO_RETENTION,
  companion_mac: process.env.NEXT_PUBLIC_FF_COMPANION_MAC,
}

export function isEnabled(flag: FeatureFlag): boolean {
  const envVal = OVERRIDES[flag]
  if (envVal === "true") return true
  if (envVal === "false") return false
  return FLAGS[flag]
}
