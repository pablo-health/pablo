// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useBillingProfile } from "@/hooks/useBillingProfile"
import { BillingProfileBanner } from "../BillingProfileBanner"
import { billingProfileGaps } from "../billingProfileGaps"
import { PracticeIdentityCard } from "../PracticeIdentityCard"
import { RenderingProviderCard } from "../RenderingProviderCard"
import { useSettingsUserStatus } from "../useSettingsPreferences"
import { WaiverPolicyCard } from "../WaiverPolicyCard"

/**
 * Billing > Practice identity.
 *
 * Who a claim is filed by — the practice on paper, and the clinician on the
 * claim. The banner names what claims still need, in the same words a claim
 * review refuses with, so it covers the contact details that now live on their
 * own page as well as the identity on this one.
 *
 * One settings item per step of billing setup. The wizard asks for practice
 * identity and billing contact as separate screens, and Settings mirrors that,
 * so "change the thing I entered on that screen" is one item rather than a
 * position in a long form.
 *
 * Every card seeds its draft from what is loaded, so nothing renders until
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
      <PracticeIdentityCard profile={profile} practiceDetails={{ name: user.practice_name }} />
      <RenderingProviderCard
        npiNumber={clinician.npi_number}
        taxonomyCode={clinician.taxonomy_code}
      />
      <WaiverPolicyCard />
    </>
  )
}
