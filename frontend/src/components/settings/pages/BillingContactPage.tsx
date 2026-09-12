// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useBillingProfile } from "@/hooks/useBillingProfile"
import { BillingContactCard } from "../BillingContactCard"
import { useSettingsUserStatus } from "../useSettingsPreferences"

/**
 * Billing > Billing contact.
 *
 * Its own settings item so that each step of billing setup has one place to go
 * back to. The wizard walks a therapist through practice identity and then
 * billing contact; if Settings folded both into a single page, "change the
 * thing I entered on that screen" would mean scrolling a long form looking for
 * the half she meant.
 */
export function BillingContactPage() {
  const { data: profile } = useBillingProfile()
  const { data: user } = useSettingsUserStatus()

  if (!profile || !user) return null

  return (
    <BillingContactCard
      profile={profile}
      practiceDetails={{ phone: user.practice_phone, address: user.practice_address }}
    />
  )
}
