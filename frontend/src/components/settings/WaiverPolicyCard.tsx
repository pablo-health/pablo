// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * WaiverPolicyCard
 *
 * The two practice-level switches a write-off is checked against: whether a
 * courtesy waiver is allowed at all, and the balance at or under which a
 * small-balance write-off is allowed without asking further. Both are off,
 * or at their conservative default, until a practice opens this card — a
 * write-off route that let money go without a stated policy would be an
 * unlogged discount.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SettingsCard, SettingsRow, Toggle } from "@/components/settings/ui"
import { useBillingProfile, useUpdateBillingProfile } from "@/hooks/useBillingProfile"
import { centsToDollars, dollarsToCents } from "@/lib/money"
import { useSettingsSaved } from "./SettingsSavedContext"

export function WaiverPolicyCard() {
  const { data: profile } = useBillingProfile()
  const update = useUpdateBillingProfile()
  const { flashSaved } = useSettingsSaved()

  const [draft, setDraft] = useState<string | null>(null)
  const [problem, setProblem] = useState<string | null>(null)

  if (!profile) return null

  const allowCourtesy = profile.allow_courtesy_writeoffs
  const thresholdValue = draft ?? centsToDollars(profile.small_balance_cents)
  const isDirty = draft !== null && draft !== centsToDollars(profile.small_balance_cents)

  function handleSaveThreshold() {
    const cents = dollarsToCents(thresholdValue)
    if (cents === null) {
      setProblem("Enter a dollar amount, like 5.00.")
      return
    }
    setProblem(null)
    update.mutate(
      { small_balance_cents: cents },
      {
        onSuccess: () => {
          setDraft(null)
          flashSaved()
        },
        onError: () => setProblem("The threshold could not be saved."),
      },
    )
  }

  return (
    <SettingsCard
      title="Waivers"
      description="What a clinician may write off from a client's balance without collecting it, and what stops it from happening by accident."
      flush
    >
      <SettingsRow
        label="Allow courtesy write-offs"
        description="A waiver with no financial-hardship or billing-error basis behind it. Off by default — a client's balance can still be written off as hardship or error either way."
      >
        <Toggle
          label="Allow courtesy write-offs"
          checked={allowCourtesy}
          disabled={update.isPending}
          onChange={(next) => update.mutate({ allow_courtesy_writeoffs: next })}
        />
      </SettingsRow>
      <SettingsRow
        label="Small-balance threshold"
        description="A balance at or under this amount may be written off as not worth chasing, without a hardship or billing reason."
      >
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">$</span>
          <Input
            value={thresholdValue}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => problem && setProblem(null)}
            className="w-24"
            inputMode="decimal"
          />
          {isDirty && (
            <Button type="button" size="sm" onClick={handleSaveThreshold} disabled={update.isPending}>
              Save
            </Button>
          )}
        </div>
      </SettingsRow>
      {problem && <p className="px-[22px] pb-3 text-[12.5px] text-red-600">{problem}</p>}
    </SettingsCard>
  )
}
