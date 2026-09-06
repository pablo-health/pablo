// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useBillingProfile } from "@/hooks/useBillingProfile"
import { BillingProfileBanner } from "../BillingProfileBanner"
import { BillingProfileCard } from "../BillingProfileCard"
import { billingProfileGaps } from "../billingProfileGaps"
import { RenderingProviderCard } from "../RenderingProviderCard"
import { useSettingsUserStatus } from "../useSettingsPreferences"

/**
 * Billing > Practice profile.
 *
 * Who a claim is filed by: the practice's legal identity and the clinician's
 * own identifiers. The banner names what claims still need, in the same
 * words a claim review refuses with.
 *
 * Both cards seed their drafts from what is loaded, so nothing renders until
 * both reads are in.
 */
export function BillingProfilePage() {
  const { data: profile } = useBillingProfile()
  const { data: user } = useSettingsUserStatus()

  if (!profile || !user) return null

  const clinician = { npi_number: user.npi_number, taxonomy_code: user.taxonomy_code }

  return (
    <>
      <BillingProfileBanner
        gaps={billingProfileGaps(profile, clinician)}
        registered={Boolean(profile.clearinghouse_provider_id)}
      />
      <BillingProfileCard profile={profile} />
      <RenderingProviderCard npiNumber={clinician.npi_number} taxonomyCode={clinician.taxonomy_code} />
    </>
  )
}
