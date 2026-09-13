// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { NpiLookupStep } from "@/components/credentialing/NpiLookupStep"

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
 */
export function CredentialingStartPage() {
  return <NpiLookupStep />
}
