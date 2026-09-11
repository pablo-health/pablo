// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { BalancesView } from "@/components/billing/BalancesView"
import { BillerExport } from "@/components/billing/BillerExport"
import { BillingSetupGate } from "@/components/billing/BillingSetupGate"
import { ClaimsSetupChecklist } from "@/components/billing/ClaimsSetupChecklist"
import { ClaimsTracker } from "@/components/billing/claims/ClaimsTracker"
import { RemittanceHolds } from "@/components/billing/claims/RemittanceHolds"
import { ReportsView } from "@/components/billing/ReportsView"
import { UnbilledQueue } from "@/components/billing/UnbilledQueue"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function BillingPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-display font-semibold text-neutral-900">Billing</h1>
        <p className="text-sm text-neutral-600 mt-1">
          Sessions that happened and haven&rsquo;t been charged yet, and the claims filed for them.
        </p>
      </div>

      {/* The gate wraps the tabs rather than sitting above them: a build whose
          billing setup is a prerequisite renders that setup here in place of
          the queue, with the nav still around it, instead of sending the
          clinician to a settings page to come back later. */}
      <BillingSetupGate>
        {/* Above the tabs: noticing that a client stopped being billed
            should not depend on picking the right tab. Renders nothing when
            nothing is held, which is almost always. */}
        <RemittanceHolds />
        <Tabs defaultValue="unbilled">
          <TabsList>
            <TabsTrigger value="unbilled" data-testid="billing-tab-unbilled">
              Unbilled
            </TabsTrigger>
            <TabsTrigger value="balances" data-testid="billing-tab-balances">
              Balances
            </TabsTrigger>
            <TabsTrigger value="claims" data-testid="billing-tab-claims">
              Claims
            </TabsTrigger>
            <TabsTrigger value="remittances" data-testid="billing-tab-remittances">
              Remittances
            </TabsTrigger>
            <TabsTrigger value="reports" data-testid="billing-tab-reports">
              Reports
            </TabsTrigger>
          </TabsList>
          <TabsContent value="unbilled" className="space-y-6">
            <UnbilledQueue />
            <BillerExport />
          </TabsContent>
          <TabsContent value="balances">
            <BalancesView />
          </TabsContent>
          {/* The checklist is guidance, not a gate: it sits above the tracker
              while setup is unfinished and never replaces it. */}
          <TabsContent value="claims" className="space-y-6">
            <ClaimsSetupChecklist />
            <ClaimsTracker />
          </TabsContent>
          <TabsContent value="remittances">
            <div className="card py-12 text-center">
              <p className="text-sm font-medium text-neutral-900">No remittances yet</p>
              <p className="mt-1 text-sm text-neutral-500">
                Payments and denials from payers land here once a claim is adjudicated.
              </p>
            </div>
          </TabsContent>
          <TabsContent value="reports">
            <ReportsView />
          </TabsContent>
        </Tabs>
      </BillingSetupGate>
    </div>
  )
}
