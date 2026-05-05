// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Simple feature flags for gating unreleased UI.
 *
 * Override at runtime via NEXT_PUBLIC_FF_<FLAG>=true in env,
 * e.g. NEXT_PUBLIC_FF_SESSION_DEFAULTS=true
 */

const FLAGS = {
  session_defaults: true,
  transcription: false,
  calendar_integrations: true,
  audio_retention: false,
} as const satisfies Record<string, boolean>

export type FeatureFlag = keyof typeof FLAGS

export function isEnabled(flag: FeatureFlag): boolean {
  const envKey = `NEXT_PUBLIC_FF_${flag.toUpperCase()}`
  const envVal = process.env[envKey]
  if (envVal === "true") return true
  if (envVal === "false") return false
  return FLAGS[flag]
}
