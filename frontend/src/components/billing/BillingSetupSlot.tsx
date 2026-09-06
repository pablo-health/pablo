// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot on the Billing page, rendered above the unbilled queue.
 *
 * Renders nothing here. A downstream build may overwrite *this file only* to
 * surface deployment-specific setup content — e.g. a prompt to connect a card
 * processor — ahead of the queue rather than inside it.
 *
 * Contract for replacements: this renders inside the Billing page for every
 * clinician visit, beneath the root providers (React Query and auth context
 * are in scope). Keep it self-gating — return null whenever there is nothing
 * to say — and keep its tests in the replacing build.
 */
export function BillingSetupSlot(): React.ReactNode {
  return null
}
