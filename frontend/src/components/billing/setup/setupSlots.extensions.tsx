// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Render slots for parts of billing setup that only some deployments can fill.
 *
 * Same merge-slot discipline as `settingsSlots.extensions.tsx`: the base build
 * renders nothing, and a downstream build replaces THIS FILE ONLY. A slot is a
 * real component rendered as `<Slot />`, never a function the wizard calls, so
 * an implementation may use hooks and fetch its own data without inheriting the
 * wizard's hook order.
 *
 * The alternative is forking the wizard, and a fork of a shared component
 * swallows every upstream fix to the screens beside the one it changed.
 */

import type { ReactNode } from "react"

/**
 * Whether this deployment can set up a card-payment processor at all.
 *
 * This flag is load-bearing and must stay in step with `PaymentsSetup` below.
 * A wizard step whose body renders nothing is a dead screen — she ticks a box,
 * clicks through to a blank page, and nothing tells her why. So the ask itself
 * is conditional: when this is false the plan screen shows no card-payments
 * row and the step is never added to her flow, which is a deployment that
 * simply does not offer the feature rather than one that offers it and then
 * cannot deliver.
 *
 * A wizard cannot tell an empty slot from a filled one by calling it — that is
 * the whole reason this is a separate flag rather than a null check.
 */
export const HAS_PAYMENTS_SETUP = false

/**
 * Connecting a processor so clients can pay an invoice by card.
 *
 * Deliberately not named for any one vendor. Which processor a deployment uses
 * — or whether it hands this to something outside Pablo entirely — is a
 * per-deployment decision, so the engine describes the capability and lets the
 * deployment supply the mechanism.
 *
 * An implementation owns the whole panel including its container, and is
 * responsible for its own loading and error states. It should not render its
 * own "skip" control: the step around it owns that, so the way out reads the
 * same on this screen as on every other.
 */
export function PaymentsSetup(): ReactNode {
  return null
}

/**
 * Whether a client can actually pay this practice by card yet.
 *
 * The last screen of setup says what is true now, and "ready to charge" is a
 * claim about a processor rather than about a box someone ticked on the way
 * past. Only a deployment that HAS a processor can answer, so the question is
 * asked here and the base build answers `null`: no processor concept, so no
 * claim either way and the ending keeps its ordinary wording.
 *
 * `null` means NOT KNOWN, not "not connected". An implementation's read is in
 * flight for a moment, and telling someone to connect a processor they already
 * connected is its own small lie — the same one in the other direction.
 */
export function usePaymentsConnected(): boolean | null {
  return null
}
