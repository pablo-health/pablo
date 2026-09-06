// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { EligibilityChecksCard } from "../EligibilityChecksCard"
import { PayersCard } from "../PayersCard"

/**
 * Billing > Insurance.
 *
 * The practice's payer list and each payer's filing deadlines, and whether a
 * client's plan is checked on its own when coverage lands. A client's own
 * coverage lives on their chart, not here.
 */
export function InsurancePage() {
  return (
    <>
      <PayersCard />
      <EligibilityChecksCard />
    </>
  )
}
