// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Product-analytics interface.
 *
 * The default implementation is a no-op (see ./noop.ts) — a stock
 * deployment ships without a product-analytics provider wired. The
 * interface is defined so onboarding UI can call
 * `trackOnboardingStepCompleted(...)` without inventing its own logging
 * path; swapping in a real provider is a one-line change in ./index.ts.
 *
 * Design notes:
 * - No PHI in any event payload — onboarding events describe provider
 *   lifecycle (which step the user reached), not patient interaction.
 * - The interface is agnostic about client vs. server execution. Real
 *   implementations can sniff `typeof window` and route accordingly.
 */

/**
 * Identifier of an onboarding step. Kept open (a plain string) so a
 * downstream build can register additional steps in its own surface
 * without widening a closed union here.
 */
export type StepId = string

export type OnboardingEvent =
  | { name: "onboarding_started"; props?: Record<string, never> }
  | { name: "onboarding_step_viewed"; props: { step: StepId } }
  | {
      name: "onboarding_step_completed"
      props: { step: StepId; [key: string]: unknown }
    }
  | { name: "onboarding_step_skipped"; props: { step: StepId } }
  | { name: "onboarding_completed"; props?: Record<string, never> }

export type AnalyticsEvent = OnboardingEvent

export type UserTraits = {
  email?: string
  practice_id?: string
  is_platform_admin?: boolean
}

export interface Analytics {
  /**
   * Associate subsequent events with a user. Call once after login.
   * Idempotent — providers dedupe.
   */
  identify(userId: string, traits?: UserTraits): void

  /**
   * Record a product event. Event names + props are constrained by the
   * {@link AnalyticsEvent} union so the type checker rejects unknown
   * events and missing required props.
   */
  track(event: AnalyticsEvent): void

  /**
   * Clear the current user association — called on logout. Safe to
   * invoke when no user was identified.
   */
  reset(): void
}
