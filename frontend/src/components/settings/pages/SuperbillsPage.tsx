// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { SettingsCard } from "../ui"

/**
 * Billing > Superbills & rates.
 *
 * Gated behind `superbills`. Most of a superbill is already in the chart, so
 * this page will mostly read from Profile and Scheduling rather than collect
 * anything new.
 */
export function SuperbillsPage() {
  return (
    <SettingsCard title="Superbills & rates">
      <p className="text-sm text-muted-foreground">
        Out-of-network receipts generated from the chart will be configured here.
      </p>
    </SettingsCard>
  )
}
