// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { BillerExport } from "@/components/billing/BillerExport"
import { BillingSetupSlot } from "@/components/billing/BillingSetupSlot"
import { UnbilledQueue } from "@/components/billing/UnbilledQueue"

export default function BillingPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">Billing</h1>
        <p className="text-sm text-neutral-600 mt-1">
          Sessions that happened and haven&rsquo;t been charged yet.
        </p>
      </div>

      <BillingSetupSlot />

      <UnbilledQueue />

      <BillerExport />
    </div>
  )
}
