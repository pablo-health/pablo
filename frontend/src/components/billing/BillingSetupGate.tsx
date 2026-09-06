// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Extension slot wrapping the working part of the Billing page.
 *
 * Renders its children unchanged here. A downstream build may overwrite *this
 * file only* to surface deployment-specific setup content — e.g. connecting a
 * card processor — and, where that setup is a prerequisite rather than an
 * upsell, render it *instead of* the children until it is done.
 *
 * It takes the queue as children rather than sitting above it so that one
 * component decides both what to say and whether the queue is usable yet. A
 * separate "is it set up" signal alongside a separate slot component is two
 * things that can disagree; this is one. A build that only wants to prompt
 * still can — render the prompt and the children together.
 *
 * Contract for replacements: this renders inside the Billing page for every
 * clinician visit, beneath the root providers (React Query and auth context
 * are in scope). Keep it self-gating — render children alone whenever there
 * is nothing to say — and keep its tests in the replacing build.
 */
export function BillingSetupGate({
  children,
}: {
  children: React.ReactNode
}): React.ReactNode {
  return <>{children}</>
}
