// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EligibilityChecksCard
 *
 * Whether a client's plan is checked on its own when coverage lands — at
 * intake, or when a plan is saved on the chart. Off leaves the chart card's
 * re-verify button as the only way to ask.
 */

"use client"

import { SettingsCard, SettingsRow, Toggle } from "@/components/settings/ui"
import { useBillingProfile, useUpdateBillingProfile } from "@/hooks/useBillingProfile"

export function EligibilityChecksCard() {
  const { data: profile } = useBillingProfile()
  const update = useUpdateBillingProfile()
  const enabled = profile?.eligibility_auto_check ?? true

  return (
    <SettingsCard
      title="Eligibility checks"
      description="Checks run through your own clearinghouse account. A check tells you what the payer knew when it was asked; it is not a promise to pay."
      flush
    >
      <SettingsRow
        label="Check the plan when coverage is saved"
        description="At intake and on the chart. Turned off, the chart card's Re-verify button still asks on demand."
      >
        <Toggle
          label="Check the plan when coverage is saved"
          checked={enabled}
          disabled={!profile || update.isPending}
          onChange={(next) => update.mutate({ eligibility_auto_check: next })}
        />
      </SettingsRow>
    </SettingsCard>
  )
}
