// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Onboarding surface contract.
 *
 * An {@link OnboardingSurface} is an ordered registry of wizard steps.
 * The active surface is resolved by `getOnboardingSurface()` (see
 * ./surface.ts) — this lets a deployment ship a different set of steps
 * (a minimal second-factor gate vs. a full guided setup) behind one
 * stable interface, the same way the auth layer selects a provider's
 * UI surfaces.
 *
 * Each step declares a `gate(status)` predicate — `true` means the user
 * has already completed this step (it can be skipped in the chain).
 *
 * Adding a step:
 *   1. Append a new entry to a surface's `steps` with the step's route
 *      path and a gate keyed off the relevant field on `UserStatus`.
 *   2. Create the step page under `app/onboarding/<slug>/page.tsx`.
 */

import type { UserStatus } from "@/lib/api/users"
import type { StepId } from "@/lib/analytics/types"

export interface StepDef {
  id: StepId
  path: string
  /** Returns true when the user has already completed this step. */
  gate: (status: UserStatus) => boolean
  /**
   * Required steps gate dashboard access (a user with one outstanding
   * required step is redirected to /onboarding by the dashboard
   * layout). Defaults to `true` so an omitted flag fails closed (the
   * step blocks the dashboard rather than being silently skippable).
   */
  required?: boolean
  /**
   * Steps with the same non-undefined group string share a base step
   * number in the "Step N of M" eyebrow and are lettered a, b, c… in
   * order (e.g. "Step 2a of 5", "Step 2b of 5").
   */
  group?: string
}

/** An ordered registry of onboarding steps for the active deployment. */
export interface OnboardingSurface {
  readonly steps: readonly StepDef[]
}

/**
 * Returns the first incomplete step, or `null` when every step's gate
 * is satisfied (i.e. onboarding is complete and the caller should route
 * to /dashboard).
 */
export function firstIncompleteStep(
  surface: OnboardingSurface,
  status: UserStatus
): StepDef | null {
  return surface.steps.find((step) => !step.gate(status)) ?? null
}

/**
 * Returns the first incomplete **required** step. The dashboard layout
 * uses this to decide whether to redirect to /onboarding: a `null`
 * return means every required gate has cleared, so the user is allowed
 * into the dashboard.
 */
export function firstIncompleteRequiredStep(
  surface: OnboardingSurface,
  status: UserStatus
): StepDef | null {
  return (
    surface.steps.find(
      (step) => step.required !== false && !step.gate(status)
    ) ?? null
  )
}

/**
 * Position of a step in the ordered list (0-based). Returns -1 if the
 * step id is not found.
 */
export function stepIndex(surface: OnboardingSurface, id: StepId): number {
  return surface.steps.findIndex((s) => s.id === id)
}

/**
 * Required-only step numbering for the "Step N of M" eyebrow.
 *
 * Excludes optional steps (required === false) and the welcome/
 * celebration bookends. Steps that share a `group` string count as one
 * base number and are lettered a, b, c… so that e.g. two grouped steps
 * display as "Step 2a of 5" and "Step 2b of 5". Steps not present in
 * the required-numbered set (optional or bookend) return null.
 */
export function requiredStepPosition(
  surface: OnboardingSurface,
  id: StepId
): { index: number; subLabel?: string; total: number; fraction: number } | null {
  const numbered = surface.steps.filter(
    (s) =>
      s.required !== false && s.id !== "welcome" && s.id !== "celebration"
  )
  const i = numbered.findIndex((s) => s.id === id)
  if (i === -1) return null

  // Assign a display index to each step; grouped steps share one index.
  let displayIdx = 0
  const seenGroups = new Map<string, number>()
  const displayIdxForStep = numbered.map((s) => {
    if (s.group) {
      if (!seenGroups.has(s.group)) {
        seenGroups.set(s.group, ++displayIdx)
      }
      return seenGroups.get(s.group)!
    }
    return ++displayIdx
  })
  const total = displayIdx

  const index = displayIdxForStep[i]
  const step = numbered[i]

  // Sub-label ('a', 'b', …) for grouped steps.
  let subLabel: string | undefined
  if (step.group) {
    const groupMembers = numbered.filter((s) => s.group === step.group)
    const subIdx = groupMembers.findIndex((s) => s.id === id)
    subLabel = String.fromCharCode(97 + subIdx)
  }

  // Progress fraction: distributed evenly within a group.
  const groupSize = step.group
    ? numbered.filter((s) => s.group === step.group).length
    : 1
  const subIdx = subLabel ? subLabel.charCodeAt(0) - 97 : 0
  const fraction = (index - 1 + (subIdx + 1) / groupSize) / total

  return { index, subLabel, total, fraction }
}
