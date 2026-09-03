// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Whether an optional feature is on for the person looking at the screen.
 *
 * Two sources, most specific first:
 *
 * 1. The account's own answer, from `featureGates.extensions.ts`. The base
 *    build has none — everyone in a deployment sees the same features. A
 *    downstream build fills that slot to answer per practice, which is how one
 *    customer gets an early look at something the rest of the deployment does
 *    not have.
 * 2. The deployment's answer, from `FEATURES_ENABLED` on the container, served
 *    through `/api/config`. This is what a self-hosted install uses, and what
 *    makes a feature live in one environment and dark in the next without a
 *    rebuild.
 *
 * Anything not named by either is off. New features are therefore dark until
 * somebody turns them on, rather than dark until somebody remembers to hide
 * them.
 */

import { useConfig } from "./config"
import { useAccountFeatures } from "./featureGates.extensions"

export interface AccountFeatures {
  /** Feature name → on. A name that is absent defers to the deployment. */
  features: Record<string, boolean>
  /**
   * False while an async implementation is still fetching. Until it settles,
   * only the deployment's answer counts, so nothing unreleased can flash into
   * view and then vanish.
   */
  resolved: boolean
}

export function useFeature(name: string | undefined): boolean {
  const { features: deploymentFeatures } = useConfig()
  const { features: accountFeatures, resolved } = useAccountFeatures()

  if (!name) return true
  if (resolved && name in accountFeatures) return accountFeatures[name]
  return deploymentFeatures?.[name] ?? false
}

/**
 * Predicate form, for filtering a list in one render. Same rules as
 * `useFeature`; call this once rather than calling the hook per item.
 */
export function useFeaturePredicate(): (name: string | undefined) => boolean {
  const { features: deploymentFeatures } = useConfig()
  const { features: accountFeatures, resolved } = useAccountFeatures()

  return (name) => {
    if (!name) return true
    if (resolved && name in accountFeatures) return accountFeatures[name]
    return deploymentFeatures?.[name] ?? false
  }
}
