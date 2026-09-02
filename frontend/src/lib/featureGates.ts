// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Whether a gated surface may render for the current account.
 *
 * Two sources, in order:
 *
 * 1. The deployment's gate slot (`featureGates.extensions.ts`). A downstream
 *    build answers per account and per environment there. A key it reports as
 *    `false` is off, full stop.
 * 2. The build-time flags in `featureFlags.ts`, for keys the deployment has no
 *    opinion about. This is what keeps a self-hosted install working with no
 *    backend to ask: set `NEXT_PUBLIC_FF_<KEY>=true` and the surface appears.
 *
 * Fail closed while the slot is still resolving, so an unreleased page never
 * flashes into view and then disappears.
 */

import { isEnabled, isKnownFlag } from "./featureFlags"
import { useFeatureGates } from "./featureGates.extensions"

/** Build-flag lookup for a key that may or may not be a known build flag. */
function buildFlag(key: string): boolean {
  const envVal = process.env[`NEXT_PUBLIC_FF_${key.toUpperCase()}`]
  if (envVal === "true") return true
  if (envVal === "false") return false
  // An undeclared key is off unless a deployment grants it. That is what makes
  // a newly added gated surface dark by default rather than dark by accident.
  return isKnownFlag(key) ? isEnabled(key) : false
}

export function useFeatureGate(key: string | undefined): boolean {
  const { gates, resolved } = useFeatureGates()
  if (!key) return true
  if (!resolved) return false
  if (key in gates) return gates[key]
  return buildFlag(key)
}

/**
 * Predicate form, for filtering a list in one render. Same rules as
 * `useFeatureGate`; call this once rather than calling the hook in a loop.
 */
export function useFeatureGatePredicate(): (key: string | undefined) => boolean {
  const { gates, resolved } = useFeatureGates()
  return (key) => {
    if (!key) return true
    if (!resolved) return false
    if (key in gates) return gates[key]
    return buildFlag(key)
  }
}
