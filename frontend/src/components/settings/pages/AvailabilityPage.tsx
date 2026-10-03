// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { BlockedTimeCard, LimitsAndBuffersCard } from "../AvailabilitySettings"
import { OtherAvailabilityRulesCard, otherRules } from "../OtherAvailabilityRulesCard"
import { WorkingHoursGrid } from "../WorkingHoursGrid"
import { useAvailabilityRules } from "@/hooks/useAvailability"
import { AvailabilityExtras } from "../settingsSlots.extensions"
import { SettingsCard } from "../ui"

/**
 * Practice > Availability.
 *
 * The calendar's window is derived from `working_hours` rules — min start to
 * max end across enabled days — rather than a separate display-hours
 * preference. `WorkingHoursGrid` is the friendly path for that one rule
 * type; `BlockedTimeCard` and `LimitsAndBuffersCard` cover the rest through
 * the same rules engine.
 */
export function AvailabilityPage() {
  const { data } = useAvailabilityRules()
  const hasOtherRules = otherRules(data?.data ?? []).length > 0

  return (
    <>
      <SettingsCard
        title="Working hours"
        description="When patients can be booked. Your calendar highlights these hours and opens at your earliest start."
      >
        <WorkingHoursGrid />
      </SettingsCard>

      <AvailabilityExtras />

      <SettingsCard title="Blocked time" description="Recurring breaks, days off and time away." flush>
        <BlockedTimeCard />
      </SettingsCard>

      <SettingsCard title="Limits & buffers" flush>
        <LimitsAndBuffersCard />
      </SettingsCard>

      {hasOtherRules && (
        <SettingsCard
          title="More rules"
          description="Limits and hours for one kind of appointment, and weekly limits."
          flush
        >
          <OtherAvailabilityRulesCard />
        </SettingsCard>
      )}
    </>
  )
}
