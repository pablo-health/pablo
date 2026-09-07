// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Render slots for content that differs *inside* a shared Billing page.
 *
 * Same idiom as `settingsSlots.extensions.tsx`: the base build renders nothing
 * from every slot, and a downstream build replaces THIS FILE ONLY. Each slot is
 * a real component rendered as `<Slot />`, not a function the page calls, so an
 * implementation may use hooks and fetch its own data without inheriting the
 * calling component's hook order.
 *
 * This is not `BillingSetupGate`. The gate answers "is the queue usable yet"
 * for a hard prerequisite; these slots add content beside work that is already
 * usable.
 */

import type { ReactNode } from "react"

/**
 * Extra rows at the end of the claims setup checklist, rendered inside its
 * list — so a deployment can add a step of its own (payer enrollment, say)
 * without forking the card. A row renders its own `<li>`.
 *
 * The card's visibility follows the core steps only: it disappears once those
 * are done, taking any extra rows with it. A step that has to outlive core
 * setup belongs in `BillingSetupGate`, which owns the whole tab, not here.
 */
export function ClaimsSetupSteps(): ReactNode {
  return null
}
