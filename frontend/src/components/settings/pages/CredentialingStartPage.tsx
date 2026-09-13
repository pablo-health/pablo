// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { NpiLookupStep } from "@/components/credentialing/NpiLookupStep"
import { PanelApplications } from "@/components/credentialing/PanelApplications"

/**
 * Billing > Credentialing.
 *
 * The same screen the billing setup wizard opens credentialing with, not a
 * second view of it — the rule `SetupSteps` names, that two surfaces over one
 * record is how the two drift apart.
 *
 * This replaces the tiered checklist as the front door. The checklist is the
 * right shape for the work and the wrong shape for the first screen: opening
 * on three tiers and fourteen cards reads as a form to survive. One number she
 * knows by heart, and we do the rest of the typing.
 *
 * Applications come FIRST, above the lookup, once she has any. Both endings of
 * the setup wizard promise her that anything a payer needs will "appear in
 * Credentialing" — a promise kept below a form she has already filled in is
 * not kept. `PanelApplications` renders nothing until there is something to
 * show, so a clinician who has not started still lands on the lookup.
 */
export function CredentialingStartPage() {
  return (
    <div className="space-y-10">
      <PanelApplications />
      <NpiLookupStep />
    </div>
  )
}
