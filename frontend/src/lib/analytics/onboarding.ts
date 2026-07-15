// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Typed helpers for onboarding-wizard analytics events.
 *
 * Call these instead of `analytics.track({ name: "onboarding_..." })`
 * directly — the wrappers stop typo'd event names from shipping and
 * keep the prop shape consistent across steps.
 */

import { analytics } from "./index"
import type { StepId } from "./types"

export function trackOnboardingStarted(): void {
  analytics.track({ name: "onboarding_started" })
}

export function trackOnboardingStepViewed(step: StepId): void {
  analytics.track({ name: "onboarding_step_viewed", props: { step } })
}

export function trackOnboardingStepCompleted(
  step: StepId,
  props?: Record<string, unknown>
): void {
  analytics.track({
    name: "onboarding_step_completed",
    props: { step, ...(props ?? {}) },
  })
}

export function trackOnboardingStepSkipped(step: StepId): void {
  analytics.track({ name: "onboarding_step_skipped", props: { step } })
}

export function trackOnboardingCompleted(): void {
  analytics.track({ name: "onboarding_completed" })
}
